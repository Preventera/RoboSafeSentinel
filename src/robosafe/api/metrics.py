"""
Collecteur de métriques Prometheus pour RoboSafe.

Expose les métriques au format Prometheus pour monitoring avec Grafana.

Métriques:
    - robosafe_safety_state: État sécurité actuel
    - robosafe_signals_total: Nombre de signaux
    - robosafe_signals_valid: Signaux valides
    - robosafe_rules_triggered: Règles déclenchées
    - robosafe_distance_min_mm: Distance minimale détectée
    - robosafe_fumes_vlep_ratio: Ratio fumées/VLEP
    - robosafe_api_requests: Requêtes API
    - robosafe_ws_clients: Clients WebSocket connectés
"""

from datetime import datetime
from typing import Any, Dict, Optional
import structlog

try:
    from prometheus_client import (
        Counter,
        Gauge,
        Histogram,
        Info,
        generate_latest,
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

logger = structlog.get_logger(__name__)


class MetricsCollector:
    """
    Collecteur de métriques pour Prometheus.
    
    Fournit des métriques en temps réel sur:
    - État de sécurité
    - Signaux capteurs
    - Règles déclenchées
    - Performance API
    """
    
    def __init__(self, prefix: str = "robosafe"):
        """
        Initialise le collecteur.
        
        Args:
            prefix: Préfixe des métriques
        """
        self._prefix = prefix
        self._enabled = PROMETHEUS_AVAILABLE
        
        if not self._enabled:
            logger.warning("prometheus_not_available")
            return
        
        # Créer registre dédié
        self._registry = CollectorRegistry()
        
        # === Métriques Info ===
        self._info = Info(
            f"{prefix}_info",
            "RoboSafe Sentinel information",
            registry=self._registry,
        )
        
        # === Gauges (valeurs instantanées) ===
        self._safety_state = Gauge(
            f"{prefix}_safety_state",
            "Current safety state code",
            registry=self._registry,
        )
        
        self._max_speed = Gauge(
            f"{prefix}_max_speed_percent",
            "Maximum allowed speed percentage",
            registry=self._registry,
        )
        
        self._signals_total = Gauge(
            f"{prefix}_signals_total",
            "Total number of signals",
            registry=self._registry,
        )
        
        self._signals_valid = Gauge(
            f"{prefix}_signals_valid",
            "Number of valid signals",
            registry=self._registry,
        )
        
        self._signals_timeout = Gauge(
            f"{prefix}_signals_timeout",
            "Number of timed out signals",
            registry=self._registry,
        )
        
        self._distance_min = Gauge(
            f"{prefix}_distance_min_mm",
            "Minimum detected distance in mm",
            registry=self._registry,
        )
        
        self._fumes_ratio = Gauge(
            f"{prefix}_fumes_vlep_ratio",
            "Fumes concentration ratio to VLEP",
            registry=self._registry,
        )
        
        self._vision_persons = Gauge(
            f"{prefix}_vision_persons_detected",
            "Number of persons detected by vision",
            registry=self._registry,
        )
        
        self._robot_speed = Gauge(
            f"{prefix}_robot_speed_mms",
            "Current robot TCP speed in mm/s",
            registry=self._registry,
        )
        
        self._ws_clients = Gauge(
            f"{prefix}_ws_clients",
            "Number of connected WebSocket clients",
            registry=self._registry,
        )
        
        # === Counters (valeurs cumulatives) ===
        self._rules_triggered = Counter(
            f"{prefix}_rules_triggered_total",
            "Total rules triggered",
            ["rule_id", "priority"],
            registry=self._registry,
        )
        
        self._state_transitions = Counter(
            f"{prefix}_state_transitions_total",
            "Total state transitions",
            ["from_state", "to_state"],
            registry=self._registry,
        )
        
        self._api_requests = Counter(
            f"{prefix}_api_requests_total",
            "Total API requests",
            ["method", "endpoint", "status"],
            registry=self._registry,
        )
        
        self._alerts = Counter(
            f"{prefix}_alerts_total",
            "Total alerts generated",
            ["level", "source"],
            registry=self._registry,
        )
        
        # === Histograms (distributions) ===
        self._api_latency = Histogram(
            f"{prefix}_api_request_duration_seconds",
            "API request duration in seconds",
            ["endpoint"],
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
            registry=self._registry,
        )
        
        self._rule_eval_time = Histogram(
            f"{prefix}_rule_evaluation_seconds",
            "Rule evaluation time in seconds",
            buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1],
            registry=self._registry,
        )
        
        logger.info("metrics_collector_initialized")
    
    def set_info(self, version: str, cell_id: str = "", mode: str = "") -> None:
        """Définit les informations statiques."""
        if not self._enabled:
            return
        
        self._info.info({
            "version": version,
            "cell_id": cell_id,
            "mode": mode,
        })
    
    def update_safety_state(self, state_code: int, max_speed: int) -> None:
        """Met à jour l'état de sécurité."""
        if not self._enabled:
            return
        
        self._safety_state.set(state_code)
        self._max_speed.set(max_speed)
    
    def update_signals(self, total: int, valid: int, timeout: int) -> None:
        """Met à jour les métriques signaux."""
        if not self._enabled:
            return
        
        self._signals_total.set(total)
        self._signals_valid.set(valid)
        self._signals_timeout.set(timeout)
    
    def update_distance(self, distance_mm: float) -> None:
        """Met à jour la distance minimale."""
        if not self._enabled:
            return
        
        if distance_mm < float('inf'):
            self._distance_min.set(distance_mm)
    
    def update_fumes(self, vlep_ratio: float) -> None:
        """Met à jour le ratio fumées."""
        if not self._enabled:
            return
        
        self._fumes_ratio.set(vlep_ratio)
    
    def update_vision(self, persons: int) -> None:
        """Met à jour les détections vision."""
        if not self._enabled:
            return
        
        self._vision_persons.set(persons)
    
    def update_robot(self, speed_mms: float) -> None:
        """Met à jour la vitesse robot."""
        if not self._enabled:
            return
        
        self._robot_speed.set(speed_mms)
    
    def update_ws_clients(self, count: int) -> None:
        """Met à jour le nombre de clients WebSocket."""
        if not self._enabled:
            return
        
        self._ws_clients.set(count)
    
    def record_rule_triggered(self, rule_id: str, priority: str) -> None:
        """Enregistre un déclenchement de règle."""
        if not self._enabled:
            return
        
        self._rules_triggered.labels(rule_id=rule_id, priority=priority).inc()
    
    def record_state_transition(self, from_state: str, to_state: str) -> None:
        """Enregistre une transition d'état."""
        if not self._enabled:
            return
        
        self._state_transitions.labels(
            from_state=from_state, 
            to_state=to_state
        ).inc()
    
    def record_api_request(
        self, 
        method: str, 
        endpoint: str, 
        status: int, 
        duration: float
    ) -> None:
        """Enregistre une requête API."""
        if not self._enabled:
            return
        
        self._api_requests.labels(
            method=method, 
            endpoint=endpoint, 
            status=str(status)
        ).inc()
        
        self._api_latency.labels(endpoint=endpoint).observe(duration)
    
    def record_alert(self, level: str, source: str) -> None:
        """Enregistre une alerte."""
        if not self._enabled:
            return
        
        self._alerts.labels(level=level, source=source).inc()
    
    def record_rule_eval_time(self, duration: float) -> None:
        """Enregistre le temps d'évaluation d'une règle."""
        if not self._enabled:
            return
        
        self._rule_eval_time.observe(duration)
    
    def update_from_state(self, state: Dict[str, Any]) -> None:
        """
        Met à jour toutes les métriques depuis un état.
        
        Args:
            state: Dictionnaire d'état du système
        """
        if not self._enabled:
            return
        
        # État sécurité
        if "state" in state:
            self.update_safety_state(
                state["state"].get("state_code", 0),
                state["state"].get("max_speed_percent", 0),
            )
        
        # Signaux
        if "signals" in state:
            signals = state["signals"]
            valid = sum(1 for s in signals.values() 
                       if isinstance(s, dict) and s.get("quality") == "good")
            self.update_signals(len(signals), valid, len(signals) - valid)
            
            # Distance
            if "scanner_min_distance" in signals:
                dist = signals["scanner_min_distance"]
                if isinstance(dist, dict):
                    self.update_distance(dist.get("value", 0))
                else:
                    self.update_distance(dist)
            
            # Fumées
            if "fumes_vlep_ratio" in signals:
                ratio = signals["fumes_vlep_ratio"]
                if isinstance(ratio, dict):
                    self.update_fumes(ratio.get("value", 0))
                else:
                    self.update_fumes(ratio)
    
    def export(self) -> str:
        """
        Exporte les métriques au format Prometheus.
        
        Returns:
            Métriques au format texte
        """
        if not self._enabled:
            return "# Prometheus client not available\n"
        
        return generate_latest(self._registry).decode("utf-8")


# Métriques personnalisées sans prometheus_client
class SimpleMetrics:
    """
    Collecteur de métriques simple (sans prometheus_client).
    
    Utilisé comme fallback si prometheus_client n'est pas installé.
    """
    
    def __init__(self):
        self._metrics: Dict[str, Any] = {
            "safety_state": 0,
            "max_speed_percent": 100,
            "signals_total": 0,
            "signals_valid": 0,
            "distance_min_mm": 0,
            "fumes_vlep_ratio": 0.0,
            "vision_persons": 0,
            "robot_speed_mms": 0.0,
            "ws_clients": 0,
            "rules_triggered": 0,
            "state_transitions": 0,
            "api_requests": 0,
            "last_updated": None,
        }
    
    def update(self, key: str, value: Any) -> None:
        """Met à jour une métrique."""
        self._metrics[key] = value
        self._metrics["last_updated"] = datetime.now().isoformat()
    
    def get(self, key: str) -> Any:
        """Récupère une métrique."""
        return self._metrics.get(key)
    
    def get_all(self) -> Dict[str, Any]:
        """Récupère toutes les métriques."""
        return self._metrics.copy()
    
    def export(self) -> str:
        """Exporte en format texte simple."""
        lines = ["# RoboSafe Simple Metrics"]
        for key, value in self._metrics.items():
            if value is not None:
                lines.append(f"robosafe_{key} {value}")
        return "\n".join(lines)
