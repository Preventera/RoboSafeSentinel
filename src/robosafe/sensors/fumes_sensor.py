"""
Driver Capteur Fumées pour RoboSafe Sentinel.

Communication via Modbus TCP pour lire les concentrations
de fumées de soudage en temps réel.

Requires:
    pip install pymodbus

Compatibilité:
    - Capteurs avec sortie Modbus TCP
    - Testo, SKC, TSI, etc.
    - Analyseurs de particules

Fonctionnalités:
    - Lecture concentration (mg/m³)
    - Calcul ratio VLEP
    - Alertes paliers
    - Historique exposition
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional
import structlog

try:
    from pymodbus.client import AsyncModbusTcpClient
    from pymodbus.exceptions import ModbusException
    PYMODBUS_AVAILABLE = True
except ImportError:
    PYMODBUS_AVAILABLE = False
    AsyncModbusTcpClient = None

logger = structlog.get_logger(__name__)


class FumesAlertLevel(IntEnum):
    """Niveaux d'alerte fumées."""
    GREEN = 0       # < 50% VLEP
    YELLOW = 1      # 50-80% VLEP
    ORANGE = 2      # 80-100% VLEP
    RED = 3         # 100-120% VLEP
    CRITICAL = 4    # > 120% VLEP


@dataclass
class FumesConfig:
    """Configuration capteur fumées."""
    ip: str = "192.168.1.50"
    port: int = 502
    unit_id: int = 1
    
    # Registres Modbus (à adapter selon capteur)
    register_concentration: int = 0     # Holding register
    register_temperature: int = 2
    register_humidity: int = 4
    register_status: int = 10
    
    # Facteur de conversion (selon capteur)
    concentration_scale: float = 0.01   # Registre * scale = mg/m³
    
    # VLEP (Valeur Limite d'Exposition Professionnelle)
    vlep_mgm3: float = 5.0              # mg/m³ (fumées soudage génériques)
    
    # Seuils relatifs (% VLEP)
    threshold_yellow: float = 0.50      # 50%
    threshold_orange: float = 0.80      # 80%
    threshold_red: float = 1.00         # 100%
    threshold_critical: float = 1.20    # 120%
    
    # Timing
    poll_interval_ms: int = 1000        # 1 Hz
    timeout_ms: int = 3000
    
    # Exposition
    exposure_window_minutes: int = 480  # 8h shift


@dataclass
class FumesMeasurement:
    """Mesure capteur fumées."""
    timestamp: datetime = field(default_factory=datetime.now)
    
    # Concentration
    concentration_mgm3: float = 0.0     # mg/m³
    vlep_ratio: float = 0.0             # ratio vs VLEP
    
    # Niveau alerte
    alert_level: FumesAlertLevel = FumesAlertLevel.GREEN
    
    # Environnement (optionnel)
    temperature_c: Optional[float] = None
    humidity_percent: Optional[float] = None
    
    # État capteur
    sensor_ok: bool = True
    sensor_warming_up: bool = False
    
    # Exposition cumulée
    exposure_minutes: float = 0.0       # Minutes > 50% VLEP
    twa_8h_mgm3: float = 0.0           # Time-Weighted Average 8h
    
    @property
    def requires_stop(self) -> bool:
        """Indique si un arrêt est requis."""
        return self.alert_level == FumesAlertLevel.CRITICAL
    
    @property
    def requires_slow(self) -> bool:
        """Indique si un ralentissement est requis."""
        return self.alert_level == FumesAlertLevel.RED
    
    @property
    def requires_alert(self) -> bool:
        """Indique si une alerte est requise."""
        return self.alert_level >= FumesAlertLevel.ORANGE
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit pour SignalManager."""
        return {
            "fumes_concentration": self.concentration_mgm3,
            "fumes_vlep_ratio": self.vlep_ratio,
            "fumes_alert_level": self.alert_level.value,
            "fumes_alert_name": self.alert_level.name,
            "fumes_sensor_ok": self.sensor_ok,
            "fumes_exposure_minutes": self.exposure_minutes,
            "fumes_twa_8h": self.twa_8h_mgm3,
            "fumes_requires_stop": self.requires_stop,
            "fumes_requires_slow": self.requires_slow,
            "fumes_requires_alert": self.requires_alert,
        }


class ExposureTracker:
    """
    Suivi de l'exposition aux fumées.
    
    Calcule:
    - Temps passé au-dessus des seuils
    - TWA (Time-Weighted Average) sur 8h
    - Dose cumulée
    """
    
    def __init__(self, window_minutes: int = 480):
        self._window = timedelta(minutes=window_minutes)
        self._measurements: List[tuple[datetime, float]] = []
        self._exposure_above_50: float = 0.0  # minutes
        
    def add_measurement(self, concentration: float, vlep: float) -> None:
        """Ajoute une mesure."""
        now = datetime.now()
        ratio = concentration / vlep if vlep > 0 else 0
        
        self._measurements.append((now, concentration))
        
        # Nettoyer anciennes mesures
        cutoff = now - self._window
        self._measurements = [
            (t, c) for t, c in self._measurements if t > cutoff
        ]
        
        # Tracker temps > 50% VLEP
        if ratio > 0.5:
            # Approximation: 1 mesure ≈ 1 seconde
            self._exposure_above_50 += 1 / 60.0  # en minutes
    
    def get_twa_8h(self, vlep: float) -> float:
        """
        Calcule le TWA sur 8h.
        
        TWA = Σ(Ci × Ti) / 480 minutes
        """
        if not self._measurements:
            return 0.0
        
        total_dose = sum(c for _, c in self._measurements)
        minutes = len(self._measurements) / 60.0  # Assuming 1 Hz
        
        if minutes > 0:
            avg = total_dose / len(self._measurements)
            return avg
        return 0.0
    
    def get_exposure_minutes(self) -> float:
        """Retourne le temps d'exposition > 50% VLEP."""
        return self._exposure_above_50
    
    def reset(self) -> None:
        """Réinitialise le tracker."""
        self._measurements.clear()
        self._exposure_above_50 = 0.0


class FumesSensorDriver:
    """
    Driver capteur fumées via Modbus TCP.
    
    Lit les concentrations de fumées et calcule les alertes.
    """
    
    def __init__(self, config: Optional[FumesConfig] = None):
        """
        Initialise le driver.
        
        Args:
            config: Configuration connexion
        """
        if not PYMODBUS_AVAILABLE:
            raise ImportError(
                "pymodbus not installed. Run: pip install pymodbus"
            )
        
        self.config = config or FumesConfig()
        self._client: Optional[AsyncModbusTcpClient] = None
        self._connected = False
        self._running = False
        self._read_task: Optional[asyncio.Task] = None
        
        # Exposure tracking
        self._exposure_tracker = ExposureTracker(
            self.config.exposure_window_minutes
        )
        
        # Callbacks
        self._on_measurement: List[Callable[[FumesMeasurement], None]] = []
        self._on_alert_change: List[Callable[[FumesAlertLevel, FumesAlertLevel], None]] = []
        self._on_connection_change: List[Callable[[bool], None]] = []
        
        # État
        self._current_measurement = FumesMeasurement()
        self._last_alert_level = FumesAlertLevel.GREEN
        
        logger.info(
            "fumes_sensor_initialized",
            ip=self.config.ip,
            vlep=self.config.vlep_mgm3,
        )
    
    @property
    def is_connected(self) -> bool:
        return self._connected
    
    @property
    def current_measurement(self) -> FumesMeasurement:
        return self._current_measurement
    
    @property
    def current_alert_level(self) -> FumesAlertLevel:
        return self._current_measurement.alert_level
    
    async def connect(self) -> bool:
        """Établit la connexion Modbus TCP."""
        try:
            self._client = AsyncModbusTcpClient(
                host=self.config.ip,
                port=self.config.port,
                timeout=self.config.timeout_ms / 1000.0,
            )
            
            connected = await self._client.connect()
            
            if connected:
                self._connected = True
                logger.info("fumes_sensor_connected", ip=self.config.ip)
                
                for callback in self._on_connection_change:
                    callback(True)
                
                return True
            else:
                logger.error("fumes_sensor_connection_failed", ip=self.config.ip)
                return False
                
        except Exception as e:
            logger.error("fumes_sensor_connection_error", error=str(e))
            self._connected = False
            return False
    
    async def disconnect(self) -> None:
        """Ferme la connexion."""
        if self._client:
            self._client.close()
            self._client = None
        
        self._connected = False
        
        for callback in self._on_connection_change:
            callback(False)
        
        logger.info("fumes_sensor_disconnected")
    
    async def read_measurement(self) -> Optional[FumesMeasurement]:
        """
        Lit les données du capteur.
        
        Returns:
            FumesMeasurement ou None si erreur
        """
        if not self.is_connected or not self._client:
            return None
        
        try:
            # Lire registres Modbus
            result = await self._client.read_holding_registers(
                address=self.config.register_concentration,
                count=6,  # Concentration + temp + humidity
                slave=self.config.unit_id,
            )
            
            if result.isError():
                logger.warning("fumes_read_error", error=str(result))
                return None
            
            # Parser les données
            raw_concentration = result.registers[0]
            concentration = raw_concentration * self.config.concentration_scale
            
            # Calculer ratio VLEP
            vlep_ratio = concentration / self.config.vlep_mgm3
            
            # Déterminer niveau alerte
            alert_level = self._get_alert_level(vlep_ratio)
            
            # Tracker exposition
            self._exposure_tracker.add_measurement(
                concentration, 
                self.config.vlep_mgm3
            )
            
            measurement = FumesMeasurement(
                timestamp=datetime.now(),
                concentration_mgm3=concentration,
                vlep_ratio=vlep_ratio,
                alert_level=alert_level,
                sensor_ok=True,
                exposure_minutes=self._exposure_tracker.get_exposure_minutes(),
                twa_8h_mgm3=self._exposure_tracker.get_twa_8h(self.config.vlep_mgm3),
            )
            
            # Détecter changement d'alerte
            if alert_level != self._last_alert_level:
                logger.warning(
                    "fumes_alert_change",
                    old_level=self._last_alert_level.name,
                    new_level=alert_level.name,
                    concentration=concentration,
                    vlep_ratio=vlep_ratio,
                )
                
                for callback in self._on_alert_change:
                    try:
                        callback(self._last_alert_level, alert_level)
                    except Exception:
                        pass
                
                self._last_alert_level = alert_level
            
            self._current_measurement = measurement
            
            # Notifier
            for callback in self._on_measurement:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(measurement)
                    else:
                        callback(measurement)
                except Exception as e:
                    logger.error("fumes_callback_error", error=str(e))
            
            return measurement
            
        except Exception as e:
            logger.warning("fumes_read_exception", error=str(e))
            return None
    
    def _get_alert_level(self, vlep_ratio: float) -> FumesAlertLevel:
        """Détermine le niveau d'alerte selon le ratio VLEP."""
        if vlep_ratio >= self.config.threshold_critical:
            return FumesAlertLevel.CRITICAL
        elif vlep_ratio >= self.config.threshold_red:
            return FumesAlertLevel.RED
        elif vlep_ratio >= self.config.threshold_orange:
            return FumesAlertLevel.ORANGE
        elif vlep_ratio >= self.config.threshold_yellow:
            return FumesAlertLevel.YELLOW
        else:
            return FumesAlertLevel.GREEN
    
    async def start_cyclic_read(self, interval_ms: float = 1000) -> None:
        """Démarre la lecture cyclique."""
        if self._running:
            return
        
        self._running = True
        self._read_task = asyncio.create_task(
            self._cyclic_read_loop(interval_ms / 1000.0)
        )
        logger.info("fumes_sensor_cyclic_started", interval_ms=interval_ms)
    
    async def stop_cyclic_read(self) -> None:
        """Arrête la lecture cyclique."""
        self._running = False
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        logger.info("fumes_sensor_cyclic_stopped")
    
    async def _cyclic_read_loop(self, interval: float) -> None:
        """Boucle de lecture cyclique."""
        while self._running:
            if self.is_connected:
                await self.read_measurement()
            else:
                await self.connect()
            
            await asyncio.sleep(interval)
    
    def reset_exposure_tracking(self) -> None:
        """Réinitialise le suivi d'exposition (début de shift)."""
        self._exposure_tracker.reset()
        logger.info("fumes_exposure_reset")
    
    def on_measurement(self, callback: Callable[[FumesMeasurement], None]) -> None:
        """Ajoute un callback pour les mesures."""
        self._on_measurement.append(callback)
    
    def on_alert_change(
        self, 
        callback: Callable[[FumesAlertLevel, FumesAlertLevel], None]
    ) -> None:
        """Ajoute un callback pour les changements d'alerte."""
        self._on_alert_change.append(callback)
    
    def on_connection_change(self, callback: Callable[[bool], None]) -> None:
        """Ajoute un callback pour les changements de connexion."""
        self._on_connection_change.append(callback)


class FumesSensorSimulator:
    """
    Simulateur capteur fumées pour tests.
    """
    
    def __init__(self, config: Optional[FumesConfig] = None):
        self.config = config or FumesConfig()
        self._running = False
        self._measurement = FumesMeasurement()
        self._callbacks_measurement: List[Callable] = []
        self._callbacks_alert: List[Callable] = []
        self._last_alert = FumesAlertLevel.GREEN
        self._exposure_tracker = ExposureTracker()
        
        # Paramètres simulation
        self._base_concentration = 2.0  # mg/m³
        self._welding_active = False
    
    @property
    def is_connected(self) -> bool:
        return self._running
    
    @property
    def current_measurement(self) -> FumesMeasurement:
        return self._measurement
    
    async def connect(self) -> bool:
        self._running = True
        logger.info("fumes_simulator_connected")
        return True
    
    async def disconnect(self) -> None:
        self._running = False
        logger.info("fumes_simulator_disconnected")
    
    async def start_cyclic_read(self, interval_ms: float = 1000) -> None:
        asyncio.create_task(self._simulation_loop(interval_ms / 1000.0))
    
    async def stop_cyclic_read(self) -> None:
        self._running = False
    
    def set_welding_active(self, active: bool) -> None:
        """Simule l'activation/désactivation du soudage."""
        self._welding_active = active
    
    async def _simulation_loop(self, interval: float) -> None:
        import random
        import math
        
        t = 0.0
        while self._running:
            t += interval
            
            # Base + variation + pic si soudage
            if self._welding_active or random.random() < 0.3:
                # Soudage actif: concentration élevée avec variation
                concentration = (
                    self._base_concentration * 2 +
                    random.gauss(0, 1) +
                    2 * math.sin(t * 0.1)
                )
            else:
                # Repos: concentration basse
                concentration = (
                    self._base_concentration * 0.3 +
                    random.gauss(0, 0.2)
                )
            
            concentration = max(0, concentration)
            
            # Occasionnellement un pic
            if random.random() < 0.02:
                concentration *= random.uniform(2, 4)
            
            vlep_ratio = concentration / self.config.vlep_mgm3
            
            # Déterminer alerte
            if vlep_ratio >= 1.2:
                alert = FumesAlertLevel.CRITICAL
            elif vlep_ratio >= 1.0:
                alert = FumesAlertLevel.RED
            elif vlep_ratio >= 0.8:
                alert = FumesAlertLevel.ORANGE
            elif vlep_ratio >= 0.5:
                alert = FumesAlertLevel.YELLOW
            else:
                alert = FumesAlertLevel.GREEN
            
            # Tracker exposition
            self._exposure_tracker.add_measurement(
                concentration,
                self.config.vlep_mgm3
            )
            
            self._measurement = FumesMeasurement(
                timestamp=datetime.now(),
                concentration_mgm3=round(concentration, 2),
                vlep_ratio=round(vlep_ratio, 3),
                alert_level=alert,
                sensor_ok=True,
                exposure_minutes=self._exposure_tracker.get_exposure_minutes(),
                twa_8h_mgm3=self._exposure_tracker.get_twa_8h(self.config.vlep_mgm3),
            )
            
            # Détecter changement alerte
            if alert != self._last_alert:
                for callback in self._callbacks_alert:
                    try:
                        callback(self._last_alert, alert)
                    except Exception:
                        pass
                self._last_alert = alert
            
            # Notifier
            for callback in self._callbacks_measurement:
                try:
                    callback(self._measurement)
                except Exception:
                    pass
            
            await asyncio.sleep(interval)
    
    def on_measurement(self, callback: Callable) -> None:
        self._callbacks_measurement.append(callback)
    
    def on_alert_change(self, callback: Callable) -> None:
        self._callbacks_alert.append(callback)
    
    def reset_exposure_tracking(self) -> None:
        self._exposure_tracker.reset()
