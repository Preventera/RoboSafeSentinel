"""
PerceptionAgent - Agent de perception (Niveaux 1-2).

Responsabilités:
- Niveau 1: Collecte des données brutes des capteurs
- Niveau 2: Normalisation, validation, filtrage

Inputs:
- Signaux capteurs (PLC, Robot, Scanner, Vision, Fumées)

Outputs:
- Données normalisées pour AnalysisAgent
- Alertes de qualité signal
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set
import structlog

from robosafe.agents.base_agent import (
    BaseAgent,
    AgentConfig,
    AgentLevel,
    AgentMessage,
    MessagePriority,
)

logger = structlog.get_logger(__name__)


class SignalQuality(Enum):
    """Qualité du signal après normalisation."""
    GOOD = "good"
    DEGRADED = "degraded"
    BAD = "bad"
    TIMEOUT = "timeout"


@dataclass
class NormalizedSignal:
    """Signal normalisé prêt pour analyse."""
    id: str
    source: str
    raw_value: Any
    normalized_value: float
    unit: str
    quality: SignalQuality
    timestamp: datetime
    
    # Métadonnées
    min_expected: Optional[float] = None
    max_expected: Optional[float] = None
    is_critical: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "value": self.normalized_value,
            "quality": self.quality.value,
            "unit": self.unit,
            "timestamp": self.timestamp.isoformat(),
            "is_critical": self.is_critical,
        }


@dataclass
class PerceptionConfig(AgentConfig):
    """Configuration de l'agent de perception."""
    name: str = "perception"
    level: AgentLevel = AgentLevel.NORMALIZE
    cycle_time_ms: int = 50  # 20 Hz
    
    # Seuils validation
    timeout_threshold_ms: float = 500.0
    max_signal_age_ms: float = 1000.0
    
    # Filtrage
    enable_smoothing: bool = True
    smoothing_alpha: float = 0.3  # Filtre EMA


class PerceptionAgent(BaseAgent):
    """
    Agent de perception - Niveaux 1-2 AgenticX5.
    
    Collecte les signaux capteurs et les normalise pour l'analyse.
    
    Traitement par signal:
    1. Validation (range, timeout)
    2. Normalisation (unités, échelle)
    3. Filtrage (lissage optionnel)
    4. Évaluation qualité
    """
    
    # Message types
    MSG_SIGNAL_UPDATE = "signal_update"
    MSG_SIGNAL_BATCH = "signal_batch"
    MSG_QUALITY_ALERT = "quality_alert"
    MSG_REQUEST_STATUS = "request_status"
    
    def __init__(self, config: Optional[PerceptionConfig] = None):
        super().__init__(config or PerceptionConfig())
        self.config: PerceptionConfig = self.config
        
        # Stockage des signaux normalisés
        self._signals: Dict[str, NormalizedSignal] = {}
        self._signal_history: Dict[str, List[float]] = {}  # Pour lissage
        
        # Définitions des signaux attendus
        self._signal_definitions: Dict[str, Dict[str, Any]] = {}
        
        # Callbacks pour injection des données capteurs
        self._sensor_callbacks: List[Callable[[], Dict[str, Any]]] = []
        
        # Stats
        self._signals_processed = 0
        self._quality_alerts = 0
        
        # Initialiser les définitions par défaut
        self._init_default_definitions()
    
    def _init_default_definitions(self) -> None:
        """Initialise les définitions de signaux par défaut."""
        self._signal_definitions = {
            # Scanner SICK
            "scanner_min_distance": {
                "source": "scanner",
                "unit": "mm",
                "min": 0,
                "max": 10000,
                "critical": True,
                "timeout_ms": 200,
            },
            "scanner_zone_status": {
                "source": "scanner",
                "unit": "",
                "min": 0,
                "max": 7,
                "critical": True,
            },
            
            # Robot Fanuc
            "fanuc_tcp_speed": {
                "source": "robot",
                "unit": "mm/s",
                "min": 0,
                "max": 2000,
                "critical": True,
            },
            "fanuc_speed_override": {
                "source": "robot",
                "unit": "%",
                "min": 0,
                "max": 100,
            },
            "fanuc_mode": {
                "source": "robot",
                "unit": "",
                "values": ["AUTO", "T1", "T2"],
                "critical": True,
            },
            
            # Vision IA
            "vision_person_count": {
                "source": "vision",
                "unit": "",
                "min": 0,
                "max": 10,
            },
            "vision_min_distance": {
                "source": "vision",
                "unit": "mm",
                "min": 0,
                "max": 10000,
                "critical": True,
            },
            "vision_ppe_ok": {
                "source": "vision",
                "unit": "",
                "values": [True, False],
            },
            
            # Fumées
            "fumes_vlep_ratio": {
                "source": "fumes",
                "unit": "",
                "min": 0,
                "max": 5.0,
                "critical": True,
            },
            "fumes_concentration": {
                "source": "fumes",
                "unit": "mg/m³",
                "min": 0,
                "max": 50,
            },
            
            # PLC Safety
            "plc_heartbeat": {
                "source": "plc",
                "unit": "",
                "critical": True,
                "timeout_ms": 100,
            },
            "estop_status": {
                "source": "plc",
                "unit": "",
                "values": [0, 1],
                "critical": True,
            },
            "door_closed": {
                "source": "plc",
                "unit": "",
                "values": [True, False],
            },
        }
    
    def add_sensor_callback(self, callback: Callable[[], Dict[str, Any]]) -> None:
        """
        Ajoute un callback pour récupérer les données capteur.
        
        Args:
            callback: Fonction retournant un dict de signaux
        """
        self._sensor_callbacks.append(callback)
    
    def add_signal_definition(
        self,
        signal_id: str,
        source: str,
        unit: str = "",
        min_val: Optional[float] = None,
        max_val: Optional[float] = None,
        critical: bool = False,
        timeout_ms: Optional[float] = None,
    ) -> None:
        """Ajoute une définition de signal."""
        self._signal_definitions[signal_id] = {
            "source": source,
            "unit": unit,
            "min": min_val,
            "max": max_val,
            "critical": critical,
            "timeout_ms": timeout_ms,
        }
    
    async def cycle(self) -> None:
        """Cycle principal: collecte et normalise les signaux."""
        # 1. Collecter les données brutes
        raw_signals = await self._collect_signals()
        
        # 2. Normaliser chaque signal
        normalized = []
        for sig_id, raw_value in raw_signals.items():
            norm_signal = self._normalize_signal(sig_id, raw_value)
            if norm_signal:
                self._signals[sig_id] = norm_signal
                normalized.append(norm_signal)
                self._signals_processed += 1
        
        # 3. Vérifier les timeouts
        self._check_timeouts()
        
        # 4. Envoyer batch à AnalysisAgent
        if normalized:
            self.send_to(
                target="analysis",
                msg_type=self.MSG_SIGNAL_BATCH,
                payload={
                    "signals": [s.to_dict() for s in normalized],
                    "timestamp": datetime.now().isoformat(),
                },
                priority=MessagePriority.HIGH,
            )
    
    async def _collect_signals(self) -> Dict[str, Any]:
        """Collecte les signaux depuis les callbacks."""
        signals = {}
        
        for callback in self._sensor_callbacks:
            try:
                data = callback()
                if data:
                    signals.update(data)
            except Exception as e:
                logger.warning(
                    "sensor_callback_error",
                    agent=self.id,
                    error=str(e),
                )
        
        return signals
    
    def _normalize_signal(
        self, 
        signal_id: str, 
        raw_value: Any
    ) -> Optional[NormalizedSignal]:
        """
        Normalise un signal brut.
        
        Args:
            signal_id: Identifiant du signal
            raw_value: Valeur brute
            
        Returns:
            Signal normalisé ou None si invalide
        """
        definition = self._signal_definitions.get(signal_id, {})
        
        # Déterminer la qualité
        quality = self._evaluate_quality(signal_id, raw_value, definition)
        
        # Convertir en valeur numérique si possible
        normalized_value = self._convert_value(raw_value)
        
        # Appliquer le lissage si activé
        if self.config.enable_smoothing and isinstance(normalized_value, (int, float)):
            normalized_value = self._apply_smoothing(signal_id, normalized_value)
        
        return NormalizedSignal(
            id=signal_id,
            source=definition.get("source", "unknown"),
            raw_value=raw_value,
            normalized_value=normalized_value,
            unit=definition.get("unit", ""),
            quality=quality,
            timestamp=datetime.now(),
            min_expected=definition.get("min"),
            max_expected=definition.get("max"),
            is_critical=definition.get("critical", False),
        )
    
    def _evaluate_quality(
        self,
        signal_id: str,
        value: Any,
        definition: Dict[str, Any],
    ) -> SignalQuality:
        """Évalue la qualité d'un signal."""
        if value is None:
            return SignalQuality.TIMEOUT
        
        # Vérifier le range
        min_val = definition.get("min")
        max_val = definition.get("max")
        
        if min_val is not None and max_val is not None:
            try:
                num_value = float(value)
                if num_value < min_val or num_value > max_val:
                    return SignalQuality.DEGRADED
            except (TypeError, ValueError):
                pass
        
        # Vérifier les valeurs autorisées
        allowed_values = definition.get("values")
        if allowed_values is not None:
            if value not in allowed_values:
                return SignalQuality.DEGRADED
        
        return SignalQuality.GOOD
    
    def _convert_value(self, raw_value: Any) -> float:
        """Convertit une valeur brute en numérique."""
        if isinstance(raw_value, (int, float)):
            return float(raw_value)
        elif isinstance(raw_value, bool):
            return 1.0 if raw_value else 0.0
        elif isinstance(raw_value, str):
            try:
                return float(raw_value)
            except ValueError:
                # Enum/string -> hash
                return float(hash(raw_value) % 1000)
        else:
            return 0.0
    
    def _apply_smoothing(self, signal_id: str, value: float) -> float:
        """Applique un filtre EMA pour lisser le signal."""
        if signal_id not in self._signal_history:
            self._signal_history[signal_id] = [value]
            return value
        
        history = self._signal_history[signal_id]
        last_value = history[-1] if history else value
        
        # Filtre EMA
        alpha = self.config.smoothing_alpha
        smoothed = alpha * value + (1 - alpha) * last_value
        
        # Garder historique limité
        history.append(smoothed)
        if len(history) > 10:
            history.pop(0)
        
        return smoothed
    
    def _check_timeouts(self) -> None:
        """Vérifie les signaux en timeout."""
        now = datetime.now()
        
        for sig_id, definition in self._signal_definitions.items():
            timeout_ms = definition.get("timeout_ms", self.config.timeout_threshold_ms)
            
            if sig_id in self._signals:
                signal = self._signals[sig_id]
                age_ms = (now - signal.timestamp).total_seconds() * 1000
                
                if age_ms > timeout_ms:
                    # Signal en timeout
                    self._signals[sig_id] = NormalizedSignal(
                        id=sig_id,
                        source=definition.get("source", "unknown"),
                        raw_value=None,
                        normalized_value=0.0,
                        unit=definition.get("unit", ""),
                        quality=SignalQuality.TIMEOUT,
                        timestamp=signal.timestamp,
                        is_critical=definition.get("critical", False),
                    )
                    
                    # Alerter si critique
                    if definition.get("critical", False):
                        self._send_quality_alert(sig_id, "timeout")
    
    def _send_quality_alert(self, signal_id: str, reason: str) -> None:
        """Envoie une alerte de qualité signal."""
        self._quality_alerts += 1
        
        self.broadcast(
            msg_type=self.MSG_QUALITY_ALERT,
            payload={
                "signal_id": signal_id,
                "reason": reason,
                "timestamp": datetime.now().isoformat(),
            },
            priority=MessagePriority.HIGH,
        )
        
        logger.warning(
            "signal_quality_alert",
            agent=self.id,
            signal=signal_id,
            reason=reason,
        )
    
    async def handle_message(self, message: AgentMessage) -> None:
        """Traite les messages reçus."""
        if message.type == self.MSG_REQUEST_STATUS:
            # Répondre avec l'état actuel
            self.send_to(
                target=message.source,
                msg_type="status_response",
                payload={
                    "signals_count": len(self._signals),
                    "signals_processed": self._signals_processed,
                    "quality_alerts": self._quality_alerts,
                },
            )
    
    def get_signal(self, signal_id: str) -> Optional[NormalizedSignal]:
        """Récupère un signal normalisé."""
        return self._signals.get(signal_id)
    
    def get_all_signals(self) -> Dict[str, NormalizedSignal]:
        """Récupère tous les signaux normalisés."""
        return self._signals.copy()
    
    def inject_signals(self, signals: Dict[str, Any]) -> None:
        """
        Injecte des signaux directement (pour simulation/test).
        
        Args:
            signals: Dict signal_id -> valeur
        """
        for sig_id, value in signals.items():
            norm = self._normalize_signal(sig_id, value)
            if norm:
                self._signals[sig_id] = norm
