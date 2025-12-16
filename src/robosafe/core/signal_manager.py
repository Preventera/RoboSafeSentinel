"""
Gestionnaire de signaux temps réel RoboSafe.

Collecte, normalise et distribue les signaux provenant des différentes sources:
- Robot (vitesse, position, mode, états)
- PLC Safety (scanners, barrières, E-stop)
- Vision IA (présence, distance, EPI)
- Capteurs (fumées, arc, etc.)
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set
from enum import Enum
import asyncio
import structlog

logger = structlog.get_logger(__name__)


class SignalSource(Enum):
    """Sources de signaux."""
    ROBOT = "robot"
    PLC_SAFETY = "plc_safety"
    SCANNER = "scanner"
    VISION = "vision"
    FUMES = "fumes"
    WELDING = "welding"
    WEARABLE = "wearable"
    ROBOSAFE = "robosafe"


class SignalQuality(Enum):
    """Qualité du signal."""
    GOOD = "good"
    DEGRADED = "degraded"
    BAD = "bad"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass
class Signal:
    """Représente un signal temps réel."""
    
    id: str
    name: str
    source: SignalSource
    value: Any
    timestamp: datetime = field(default_factory=datetime.now)
    quality: SignalQuality = SignalQuality.GOOD
    unit: str = ""
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    fail_safe_value: Any = None
    
    @property
    def age_ms(self) -> float:
        """Âge du signal en millisecondes."""
        return (datetime.now() - self.timestamp).total_seconds() * 1000
    
    @property
    def is_valid(self) -> bool:
        """Indique si le signal est valide."""
        return self.quality in (SignalQuality.GOOD, SignalQuality.DEGRADED)
    
    def to_dict(self) -> Dict:
        """Convertit en dictionnaire."""
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source.value,
            "value": self.value,
            "timestamp": self.timestamp.isoformat(),
            "quality": self.quality.value,
            "unit": self.unit,
            "age_ms": self.age_ms,
        }


@dataclass
class SignalDefinition:
    """Définition d'un signal (métadonnées)."""
    
    id: str
    name: str
    source: SignalSource
    data_type: str  # bool, int, float, enum, bitfield
    unit: str = ""
    frequency_hz: float = 10.0
    timeout_ms: float = 1000.0
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    fail_safe_value: Any = None
    description: str = ""
    critical: bool = False


class SignalManager:
    """
    Gestionnaire centralisé des signaux.
    
    Responsabilités:
    - Enregistrer les définitions de signaux
    - Recevoir et stocker les valeurs
    - Détecter les timeouts
    - Appliquer les valeurs fail-safe
    - Notifier les abonnés
    """
    
    def __init__(self, watchdog_interval_ms: float = 100.0):
        """
        Initialise le gestionnaire.
        
        Args:
            watchdog_interval_ms: Intervalle de vérification des timeouts
        """
        self._definitions: Dict[str, SignalDefinition] = {}
        self._signals: Dict[str, Signal] = {}
        self._subscribers: Dict[str, List[Callable[[Signal], None]]] = {}
        self._global_subscribers: List[Callable[[Signal], None]] = []
        self._watchdog_interval = watchdog_interval_ms / 1000.0
        self._running = False
        self._lock = asyncio.Lock()
        self._watchdog_task: Optional[asyncio.Task] = None
        
        # Stats
        self._update_count = 0
        self._timeout_count = 0
        
        logger.info("signal_manager_initialized")
    
    def register_signal(self, definition: SignalDefinition) -> None:
        """
        Enregistre une définition de signal.
        
        Args:
            definition: Définition du signal
        """
        self._definitions[definition.id] = definition
        
        # Initialiser avec fail-safe
        self._signals[definition.id] = Signal(
            id=definition.id,
            name=definition.name,
            source=definition.source,
            value=definition.fail_safe_value,
            quality=SignalQuality.UNKNOWN,
            unit=definition.unit,
            min_value=definition.min_value,
            max_value=definition.max_value,
            fail_safe_value=definition.fail_safe_value,
        )
        
        logger.debug("signal_registered", signal_id=definition.id)
    
    def register_signals(self, definitions: List[SignalDefinition]) -> None:
        """Enregistre plusieurs signaux."""
        for definition in definitions:
            self.register_signal(definition)
    
    async def update_signal(
        self,
        signal_id: str,
        value: Any,
        quality: SignalQuality = SignalQuality.GOOD,
        timestamp: Optional[datetime] = None,
    ) -> bool:
        """
        Met à jour la valeur d'un signal.
        
        Args:
            signal_id: ID du signal
            value: Nouvelle valeur
            quality: Qualité du signal
            timestamp: Horodatage (now si None)
            
        Returns:
            True si mise à jour réussie
        """
        async with self._lock:
            if signal_id not in self._definitions:
                logger.warning("unknown_signal_update", signal_id=signal_id)
                return False
            
            definition = self._definitions[signal_id]
            
            signal = Signal(
                id=signal_id,
                name=definition.name,
                source=definition.source,
                value=value,
                timestamp=timestamp or datetime.now(),
                quality=quality,
                unit=definition.unit,
                min_value=definition.min_value,
                max_value=definition.max_value,
                fail_safe_value=definition.fail_safe_value,
            )
            
            self._signals[signal_id] = signal
            self._update_count += 1
        
        # Notifier (hors du lock)
        await self._notify_subscribers(signal)
        
        return True
    
    async def update_signals_batch(
        self,
        updates: Dict[str, Any],
        quality: SignalQuality = SignalQuality.GOOD,
    ) -> int:
        """
        Met à jour plusieurs signaux en batch.
        
        Args:
            updates: Dict {signal_id: value}
            quality: Qualité commune
            
        Returns:
            Nombre de signaux mis à jour
        """
        count = 0
        timestamp = datetime.now()
        
        for signal_id, value in updates.items():
            if await self.update_signal(signal_id, value, quality, timestamp):
                count += 1
        
        return count
    
    def get_signal(self, signal_id: str) -> Optional[Signal]:
        """
        Récupère un signal.
        
        Args:
            signal_id: ID du signal
            
        Returns:
            Signal ou None si non trouvé
        """
        return self._signals.get(signal_id)
    
    def get_signal_value(
        self, 
        signal_id: str, 
        default: Any = None,
        use_failsafe_if_invalid: bool = True,
    ) -> Any:
        """
        Récupère la valeur d'un signal.
        
        Args:
            signal_id: ID du signal
            default: Valeur par défaut
            use_failsafe_if_invalid: Utiliser fail-safe si signal invalide
            
        Returns:
            Valeur du signal
        """
        signal = self._signals.get(signal_id)
        
        if signal is None:
            return default
        
        if not signal.is_valid and use_failsafe_if_invalid:
            return signal.fail_safe_value
        
        return signal.value
    
    def get_signals_by_source(self, source: SignalSource) -> List[Signal]:
        """Récupère tous les signaux d'une source."""
        return [s for s in self._signals.values() if s.source == source]
    
    def get_all_signals(self) -> Dict[str, Signal]:
        """Récupère tous les signaux (copie)."""
        return self._signals.copy()
    
    def subscribe(
        self, 
        signal_id: str, 
        callback: Callable[[Signal], None]
    ) -> None:
        """
        S'abonne aux mises à jour d'un signal.
        
        Args:
            signal_id: ID du signal
            callback: Fonction appelée à chaque mise à jour
        """
        if signal_id not in self._subscribers:
            self._subscribers[signal_id] = []
        self._subscribers[signal_id].append(callback)
    
    def subscribe_all(self, callback: Callable[[Signal], None]) -> None:
        """S'abonne à tous les signaux."""
        self._global_subscribers.append(callback)
    
    async def _notify_subscribers(self, signal: Signal) -> None:
        """Notifie les abonnés."""
        # Abonnés spécifiques
        for callback in self._subscribers.get(signal.id, []):
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(signal)
                else:
                    callback(signal)
            except Exception as e:
                logger.error(
                    "subscriber_callback_error",
                    signal_id=signal.id,
                    error=str(e),
                )
        
        # Abonnés globaux
        for callback in self._global_subscribers:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(signal)
                else:
                    callback(signal)
            except Exception as e:
                logger.error("global_subscriber_error", error=str(e))
    
    async def start_watchdog(self) -> None:
        """Démarre le watchdog de timeout."""
        if self._running:
            return
        
        self._running = True
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        logger.info("signal_watchdog_started")
    
    async def stop_watchdog(self) -> None:
        """Arrête le watchdog."""
        self._running = False
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
        logger.info("signal_watchdog_stopped")
    
    async def _watchdog_loop(self) -> None:
        """Boucle de surveillance des timeouts."""
        while self._running:
            await asyncio.sleep(self._watchdog_interval)
            await self._check_timeouts()
    
    async def _check_timeouts(self) -> None:
        """Vérifie les timeouts des signaux."""
        async with self._lock:
            now = datetime.now()
            
            for signal_id, signal in self._signals.items():
                definition = self._definitions.get(signal_id)
                if not definition:
                    continue
                
                age_ms = (now - signal.timestamp).total_seconds() * 1000
                
                if age_ms > definition.timeout_ms and signal.quality != SignalQuality.TIMEOUT:
                    # Signal en timeout
                    self._signals[signal_id] = Signal(
                        id=signal_id,
                        name=signal.name,
                        source=signal.source,
                        value=signal.fail_safe_value,  # Appliquer fail-safe
                        timestamp=signal.timestamp,
                        quality=SignalQuality.TIMEOUT,
                        unit=signal.unit,
                        fail_safe_value=signal.fail_safe_value,
                    )
                    
                    self._timeout_count += 1
                    
                    logger.warning(
                        "signal_timeout",
                        signal_id=signal_id,
                        age_ms=age_ms,
                        timeout_ms=definition.timeout_ms,
                        fail_safe_value=signal.fail_safe_value,
                        critical=definition.critical,
                    )
    
    def get_stats(self) -> Dict:
        """Retourne les statistiques."""
        valid_count = sum(1 for s in self._signals.values() if s.is_valid)
        
        return {
            "total_signals": len(self._signals),
            "valid_signals": valid_count,
            "invalid_signals": len(self._signals) - valid_count,
            "update_count": self._update_count,
            "timeout_count": self._timeout_count,
            "subscriber_count": sum(len(subs) for subs in self._subscribers.values()),
            "global_subscriber_count": len(self._global_subscribers),
        }


# Signaux pré-définis pour cellule soudage MIG
def get_welding_cell_signals() -> List[SignalDefinition]:
    """Retourne les définitions de signaux pour une cellule de soudage."""
    return [
        # Robot Fanuc
        SignalDefinition(
            id="fanuc_tcp_speed",
            name="Vitesse TCP",
            source=SignalSource.ROBOT,
            data_type="float",
            unit="mm/s",
            frequency_hz=100,
            timeout_ms=100,
            min_value=0,
            max_value=2000,
            fail_safe_value=0,
            critical=True,
        ),
        SignalDefinition(
            id="fanuc_mode",
            name="Mode robot",
            source=SignalSource.ROBOT,
            data_type="enum",
            frequency_hz=10,
            timeout_ms=500,
            fail_safe_value="T1",
            critical=True,
        ),
        SignalDefinition(
            id="fanuc_servo_on",
            name="Servos activés",
            source=SignalSource.ROBOT,
            data_type="bool",
            frequency_hz=100,
            timeout_ms=100,
            fail_safe_value=False,
            critical=True,
        ),
        
        # PLC Safety
        SignalDefinition(
            id="plc_heartbeat",
            name="Heartbeat PLC",
            source=SignalSource.PLC_SAFETY,
            data_type="int",
            frequency_hz=100,
            timeout_ms=500,
            fail_safe_value=0,
            critical=True,
        ),
        SignalDefinition(
            id="scanner_zone_status",
            name="État zones scanner",
            source=SignalSource.SCANNER,
            data_type="bitfield",
            frequency_hz=50,
            timeout_ms=100,
            fail_safe_value=0xFF,  # Toutes zones en protection
            critical=True,
        ),
        SignalDefinition(
            id="scanner_min_distance",
            name="Distance min scanner",
            source=SignalSource.SCANNER,
            data_type="int",
            unit="mm",
            frequency_hz=50,
            timeout_ms=100,
            min_value=0,
            max_value=8000,
            fail_safe_value=0,  # Distance 0 = prudent
            critical=True,
        ),
        SignalDefinition(
            id="estop_status",
            name="Arrêts urgence",
            source=SignalSource.PLC_SAFETY,
            data_type="bitfield",
            frequency_hz=100,
            timeout_ms=100,
            fail_safe_value=0xFF,  # Tous E-stop actifs
            critical=True,
        ),
        
        # Soudage
        SignalDefinition(
            id="arc_on",
            name="Arc actif",
            source=SignalSource.WELDING,
            data_type="bool",
            frequency_hz=100,
            timeout_ms=200,
            fail_safe_value=False,
            critical=False,
        ),
        SignalDefinition(
            id="weld_current",
            name="Courant soudage",
            source=SignalSource.WELDING,
            data_type="float",
            unit="A",
            frequency_hz=10,
            timeout_ms=1000,
            min_value=0,
            max_value=500,
            fail_safe_value=0,
        ),
        
        # Fumées
        SignalDefinition(
            id="fumes_concentration",
            name="Concentration fumées",
            source=SignalSource.FUMES,
            data_type="float",
            unit="mg/m³",
            frequency_hz=1,
            timeout_ms=5000,
            min_value=0,
            max_value=50,
            fail_safe_value=50,  # Max = prudent
            critical=False,
        ),
        SignalDefinition(
            id="fumes_vlep_ratio",
            name="Ratio fumées/VLEP",
            source=SignalSource.ROBOSAFE,
            data_type="float",
            frequency_hz=1,
            timeout_ms=5000,
            min_value=0,
            max_value=3.0,
            fail_safe_value=1.0,  # 100% VLEP
        ),
        
        # Vision IA
        SignalDefinition(
            id="vision_presence",
            name="Présence détectée",
            source=SignalSource.VISION,
            data_type="bool",
            frequency_hz=30,
            timeout_ms=500,
            fail_safe_value=True,  # Présence = prudent
            critical=True,
        ),
        SignalDefinition(
            id="vision_min_distance",
            name="Distance min personne",
            source=SignalSource.VISION,
            data_type="int",
            unit="mm",
            frequency_hz=30,
            timeout_ms=500,
            min_value=0,
            max_value=10000,
            fail_safe_value=0,  # Distance 0 = prudent
            critical=True,
        ),
        SignalDefinition(
            id="vision_confidence",
            name="Confiance détection",
            source=SignalSource.VISION,
            data_type="float",
            frequency_hz=30,
            timeout_ms=500,
            min_value=0,
            max_value=1.0,
            fail_safe_value=0,  # Confiance 0 = marges augmentées
        ),
        
        # RoboSafe interne
        SignalDefinition(
            id="robosafe_risk_score",
            name="Score risque",
            source=SignalSource.ROBOSAFE,
            data_type="float",
            frequency_hz=10,
            timeout_ms=1000,
            min_value=0,
            max_value=100,
            fail_safe_value=100,  # Score max = prudent
        ),
    ]
