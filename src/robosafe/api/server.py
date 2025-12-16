"""
Serveur API RoboSafe Sentinel.

API REST + WebSocket pour:
- Monitoring temps réel
- Configuration
- Alertes
- Métriques Prometheus

Endpoints:
    GET  /health          - Health check
    GET  /api/v1/status   - État global système
    GET  /api/v1/signals  - Tous les signaux
    GET  /api/v1/alerts   - Alertes actives
    POST /api/v1/command  - Envoyer commande
    WS   /ws              - WebSocket temps réel
    GET  /metrics         - Métriques Prometheus
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel, Field
import structlog

from robosafe import __version__
from robosafe.api.websocket_manager import WebSocketManager
from robosafe.api.metrics import MetricsCollector

logger = structlog.get_logger(__name__)

# Gestionnaires globaux (injectés au démarrage)
_signal_manager = None
_state_machine = None
_rule_engine = None
_ws_manager = WebSocketManager()
_metrics = MetricsCollector()


# ============== Modèles Pydantic ==============

class HealthResponse(BaseModel):
    """Réponse health check."""
    status: str = "ok"
    version: str = __version__
    timestamp: datetime = Field(default_factory=datetime.now)


class SystemStatus(BaseModel):
    """État global du système."""
    timestamp: datetime = Field(default_factory=datetime.now)
    version: str = __version__
    
    # État sécurité
    safety_state: str = "UNKNOWN"
    safety_state_code: int = 0
    max_speed_percent: int = 0
    allows_production: bool = False
    state_duration_seconds: float = 0.0
    
    # Signaux
    total_signals: int = 0
    valid_signals: int = 0
    timeout_signals: int = 0
    
    # Règles
    rules_total: int = 0
    rules_triggered: int = 0
    
    # Connexions
    plc_connected: bool = False
    robot_connected: bool = False
    vision_connected: bool = False
    
    # WebSocket
    ws_clients: int = 0


class SignalValue(BaseModel):
    """Valeur d'un signal."""
    id: str
    name: str
    value: Any
    quality: str
    unit: str = ""
    timestamp: datetime
    age_ms: float


class SignalsResponse(BaseModel):
    """Liste des signaux."""
    timestamp: datetime = Field(default_factory=datetime.now)
    count: int
    signals: List[SignalValue]


class Alert(BaseModel):
    """Alerte active."""
    id: str
    level: str  # CRITICAL, HIGH, MEDIUM, LOW
    message: str
    source: str
    timestamp: datetime
    acknowledged: bool = False


class AlertsResponse(BaseModel):
    """Liste des alertes."""
    timestamp: datetime = Field(default_factory=datetime.now)
    count: int
    alerts: List[Alert]


class CommandRequest(BaseModel):
    """Requête de commande."""
    command: str  # ESTOP, STOP, SLOW_50, SLOW_25, RESET, CLEAR
    reason: str = ""
    operator_id: Optional[str] = None


class CommandResponse(BaseModel):
    """Réponse à une commande."""
    success: bool
    command: str
    message: str
    timestamp: datetime = Field(default_factory=datetime.now)


class RuleInfo(BaseModel):
    """Information sur une règle."""
    id: str
    name: str
    priority: str
    enabled: bool
    trigger_count: int
    last_triggered: Optional[datetime] = None


class RulesResponse(BaseModel):
    """Liste des règles."""
    timestamp: datetime = Field(default_factory=datetime.now)
    count: int
    rules: List[RuleInfo]


# ============== Lifespan ==============

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestion du cycle de vie de l'application."""
    logger.info("api_server_starting", version=__version__)
    
    # Démarrer le broadcast WebSocket
    asyncio.create_task(_ws_broadcast_loop())
    
    yield
    
    logger.info("api_server_stopping")
    await _ws_manager.disconnect_all()


# ============== Application FastAPI ==============

app = FastAPI(
    title="RoboSafe Sentinel API",
    description="API de supervision sécurité pour cellules robotisées",
    version=__version__,
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En production, restreindre
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Fichiers statiques (Dashboard)
static_path = Path(__file__).parent / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


# ============== Injection des gestionnaires ==============

def init_api(signal_manager, state_machine, rule_engine):
    """
    Initialise l'API avec les gestionnaires.
    
    Args:
        signal_manager: Gestionnaire de signaux
        state_machine: Machine d'états
        rule_engine: Moteur de règles
    """
    global _signal_manager, _state_machine, _rule_engine
    _signal_manager = signal_manager
    _state_machine = state_machine
    _rule_engine = rule_engine
    
    logger.info("api_initialized")


# ============== Endpoints REST ==============

@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """Health check endpoint."""
    return HealthResponse()


@app.get("/api/v1/status", response_model=SystemStatus, tags=["Monitoring"])
async def get_system_status():
    """Retourne l'état global du système."""
    status = SystemStatus()
    
    if _state_machine:
        sm_status = _state_machine.get_status()
        status.safety_state = sm_status.get("current_state", "UNKNOWN")
        status.safety_state_code = sm_status.get("state_code", 0)
        status.max_speed_percent = sm_status.get("max_speed_percent", 0)
        status.allows_production = sm_status.get("allows_production", False)
        status.state_duration_seconds = sm_status.get("state_duration_seconds", 0)
    
    if _signal_manager:
        sig_stats = _signal_manager.get_stats()
        status.total_signals = sig_stats.get("total_signals", 0)
        status.valid_signals = sig_stats.get("valid_signals", 0)
        status.timeout_signals = sig_stats.get("invalid_signals", 0)
    
    if _rule_engine:
        rule_stats = _rule_engine.get_stats()
        status.rules_total = rule_stats.get("total_rules", 0)
        status.rules_triggered = rule_stats.get("trigger_count", 0)
    
    status.ws_clients = _ws_manager.client_count
    
    return status


@app.get("/api/v1/signals", response_model=SignalsResponse, tags=["Signals"])
async def get_signals(
    source: Optional[str] = Query(None, description="Filtrer par source"),
    quality: Optional[str] = Query(None, description="Filtrer par qualité"),
):
    """Retourne tous les signaux."""
    if not _signal_manager:
        raise HTTPException(status_code=503, detail="Signal manager not available")
    
    signals = _signal_manager.get_all_signals()
    
    signal_list = []
    for sig_id, signal in signals.items():
        # Filtres
        if source and signal.source.value != source:
            continue
        if quality and signal.quality.value != quality:
            continue
        
        signal_list.append(SignalValue(
            id=signal.id,
            name=signal.name,
            value=signal.value,
            quality=signal.quality.value,
            unit=signal.unit,
            timestamp=signal.timestamp,
            age_ms=signal.age_ms,
        ))
    
    return SignalsResponse(
        count=len(signal_list),
        signals=signal_list,
    )


@app.get("/api/v1/signals/{signal_id}", response_model=SignalValue, tags=["Signals"])
async def get_signal(signal_id: str):
    """Retourne un signal spécifique."""
    if not _signal_manager:
        raise HTTPException(status_code=503, detail="Signal manager not available")
    
    signal = _signal_manager.get_signal(signal_id)
    
    if not signal:
        raise HTTPException(status_code=404, detail=f"Signal {signal_id} not found")
    
    return SignalValue(
        id=signal.id,
        name=signal.name,
        value=signal.value,
        quality=signal.quality.value,
        unit=signal.unit,
        timestamp=signal.timestamp,
        age_ms=signal.age_ms,
    )


@app.get("/api/v1/alerts", response_model=AlertsResponse, tags=["Alerts"])
async def get_alerts(
    level: Optional[str] = Query(None, description="Filtrer par niveau"),
    limit: int = Query(50, ge=1, le=500),
):
    """Retourne les alertes actives."""
    # Pour l'instant, alertes mockées
    # En production: récupérer depuis un store d'alertes
    alerts = []
    
    return AlertsResponse(
        count=len(alerts),
        alerts=alerts,
    )


@app.post("/api/v1/command", response_model=CommandResponse, tags=["Commands"])
async def send_command(request: CommandRequest):
    """Envoie une commande au système."""
    if not _state_machine:
        raise HTTPException(status_code=503, detail="State machine not available")
    
    command = request.command.upper()
    success = False
    message = ""
    
    try:
        if command == "ESTOP":
            success = await _state_machine.request_estop(
                trigger=f"API: {request.reason}",
            )
            message = "E-STOP requested"
        
        elif command == "STOP":
            success = await _state_machine.request_stop(
                trigger=f"API: {request.reason}",
            )
            message = "Stop requested"
        
        elif command == "SLOW_50":
            success = await _state_machine.request_slow(
                speed_percent=50,
                trigger=f"API: {request.reason}",
            )
            message = "Slow 50% requested"
        
        elif command == "SLOW_25":
            success = await _state_machine.request_slow(
                speed_percent=25,
                trigger=f"API: {request.reason}",
            )
            message = "Slow 25% requested"
        
        elif command == "RESET":
            success = await _state_machine.request_recovery(
                trigger=f"API: {request.reason}",
            )
            message = "Reset/Recovery requested"
        
        elif command == "NORMAL":
            success = await _state_machine.request_normal(
                trigger=f"API: {request.reason}",
            )
            message = "Normal mode requested"
        
        else:
            raise HTTPException(
                status_code=400, 
                detail=f"Unknown command: {command}"
            )
        
        logger.info(
            "api_command",
            command=command,
            success=success,
            operator=request.operator_id,
            reason=request.reason,
        )
        
    except Exception as e:
        logger.error("api_command_error", command=command, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
    
    return CommandResponse(
        success=success,
        command=command,
        message=message,
    )


@app.get("/api/v1/rules", response_model=RulesResponse, tags=["Rules"])
async def get_rules():
    """Retourne la liste des règles."""
    if not _rule_engine:
        raise HTTPException(status_code=503, detail="Rule engine not available")
    
    rules = []
    for rule_id, rule in _rule_engine._rules.items():
        rules.append(RuleInfo(
            id=rule.id,
            name=rule.name,
            priority=rule.priority.name,
            enabled=rule.enabled,
            trigger_count=rule._trigger_count,
            last_triggered=rule._last_triggered,
        ))
    
    return RulesResponse(
        count=len(rules),
        rules=rules,
    )


@app.post("/api/v1/rules/{rule_id}/enable", tags=["Rules"])
async def enable_rule(rule_id: str):
    """Active une règle."""
    if not _rule_engine:
        raise HTTPException(status_code=503, detail="Rule engine not available")
    
    if _rule_engine.enable_rule(rule_id):
        return {"success": True, "rule_id": rule_id, "enabled": True}
    
    raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")


@app.post("/api/v1/rules/{rule_id}/disable", tags=["Rules"])
async def disable_rule(rule_id: str):
    """Désactive une règle."""
    if not _rule_engine:
        raise HTTPException(status_code=503, detail="Rule engine not available")
    
    if _rule_engine.disable_rule(rule_id):
        return {"success": True, "rule_id": rule_id, "enabled": False}
    
    raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")


# ============== Métriques Prometheus ==============

@app.get("/metrics", response_class=PlainTextResponse, tags=["Metrics"])
async def get_metrics():
    """Retourne les métriques au format Prometheus."""
    return _metrics.export()


# ============== WebSocket ==============

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket pour streaming temps réel."""
    await _ws_manager.connect(websocket)
    
    try:
        while True:
            # Recevoir messages du client (commandes, ping, etc.)
            data = await websocket.receive_text()
            
            # Traiter les commandes WebSocket si nécessaire
            if data == "ping":
                await websocket.send_text("pong")
            
    except WebSocketDisconnect:
        _ws_manager.disconnect(websocket)
    except Exception as e:
        logger.warning("websocket_error", error=str(e))
        _ws_manager.disconnect(websocket)


async def _ws_broadcast_loop():
    """Boucle de broadcast WebSocket."""
    while True:
        try:
            if _ws_manager.client_count > 0:
                # Construire le message
                message = {
                    "type": "update",
                    "timestamp": datetime.now().isoformat(),
                }
                
                # État sécurité
                if _state_machine:
                    message["state"] = _state_machine.get_status()
                
                # Signaux critiques
                if _signal_manager:
                    signals = _signal_manager.get_all_signals()
                    message["signals"] = {
                        sig_id: {
                            "value": sig.value,
                            "quality": sig.quality.value,
                        }
                        for sig_id, sig in signals.items()
                    }
                
                # Métriques
                _metrics.update_from_state(message)
                
                # Broadcast
                await _ws_manager.broadcast(message)
        
        except Exception as e:
            logger.debug("ws_broadcast_error", error=str(e))
        
        await asyncio.sleep(0.1)  # 10 Hz


# ============== Démarrage standalone ==============

def run(host: str = "0.0.0.0", port: int = 8080):
    """Lance le serveur API."""
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
