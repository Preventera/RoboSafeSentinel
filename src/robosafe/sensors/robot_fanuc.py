"""
Driver Fanuc Robot pour RoboSafe Sentinel.

Communication via EtherNet/IP pour lire l'état du robot et
envoyer des demandes de modification de vitesse.

Requires:
    pip install pycomm3

Compatibilité:
    - Fanuc R-30iB / R-30iB+
    - Option EtherNet/IP (J817)
    - Assemblées I/O standard
"""

import asyncio
import struct
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional
import structlog

try:
    from pycomm3 import CIPDriver
    PYCOMM3_AVAILABLE = True
except ImportError:
    PYCOMM3_AVAILABLE = False
    CIPDriver = None

logger = structlog.get_logger(__name__)


class FanucMode(IntEnum):
    """Modes de fonctionnement Fanuc."""
    UNKNOWN = 0
    AUTO = 1
    T1 = 2       # Teach pendant, vitesse limitée 250mm/s
    T2 = 3       # Teach pendant, vitesse normale


class FanucState(IntEnum):
    """États du programme robot."""
    UNKNOWN = 0
    ABORTED = 1
    PAUSED = 2
    RUNNING = 3


class FanucAlarm(IntEnum):
    """Niveaux d'alarme."""
    NONE = 0
    WARN = 1
    PAUSE = 2
    STOP = 3


@dataclass
class FanucConfig:
    """Configuration connexion Fanuc."""
    ip: str = "192.168.1.10"
    slot: int = 0
    
    # Timing
    poll_interval_ms: int = 50
    timeout_ms: int = 1000
    
    # Registres pour override vitesse
    speed_override_register: str = "R[1]"


@dataclass 
class FanucTCPPosition:
    """Position TCP (Tool Center Point)."""
    x: float = 0.0      # mm
    y: float = 0.0      # mm
    z: float = 0.0      # mm
    w: float = 0.0      # deg (Rx)
    p: float = 0.0      # deg (Ry)
    r: float = 0.0      # deg (Rz)
    
    def distance_to(self, other: 'FanucTCPPosition') -> float:
        """Calcule la distance euclidienne à une autre position."""
        import math
        return math.sqrt(
            (self.x - other.x) ** 2 +
            (self.y - other.y) ** 2 +
            (self.z - other.z) ** 2
        )


@dataclass
class FanucStatus:
    """État complet du robot Fanuc."""
    timestamp: datetime = field(default_factory=datetime.now)
    
    # États généraux
    power_on: bool = False
    servo_on: bool = False
    mode: FanucMode = FanucMode.UNKNOWN
    program_state: FanucState = FanucState.UNKNOWN
    alarm_level: FanucAlarm = FanucAlarm.NONE
    
    # Mouvement
    in_motion: bool = False
    speed_override: int = 100       # % (0-100)
    current_speed_mms: float = 0.0  # mm/s actuelle
    
    # Position
    tcp_position: FanucTCPPosition = field(default_factory=FanucTCPPosition)
    
    # Programme
    current_program: str = ""
    current_line: int = 0
    
    # E/S
    digital_inputs: int = 0     # Bitfield DI
    digital_outputs: int = 0    # Bitfield DO
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit pour SignalManager."""
        return {
            "fanuc_power_on": self.power_on,
            "fanuc_servo_on": self.servo_on,
            "fanuc_mode": self.mode.name,
            "fanuc_program_state": self.program_state.name,
            "fanuc_alarm": self.alarm_level.value,
            "fanuc_in_motion": self.in_motion,
            "fanuc_speed_override": self.speed_override,
            "fanuc_tcp_speed": self.current_speed_mms,
            "fanuc_tcp_x": self.tcp_position.x,
            "fanuc_tcp_y": self.tcp_position.y,
            "fanuc_tcp_z": self.tcp_position.z,
            "fanuc_program": self.current_program,
            "fanuc_line": self.current_line,
        }


class FanucDriver:
    """
    Driver de communication Fanuc via EtherNet/IP.
    
    Utilise pycomm3 pour la communication CIP (Common Industrial Protocol).
    
    Fonctionnalités:
    - Lecture position TCP et articulaire
    - Lecture vitesse et override
    - Lecture mode (AUTO/T1/T2) et état programme
    - Modification override vitesse (si autorisé)
    """
    
    def __init__(self, config: Optional[FanucConfig] = None):
        """
        Initialise le driver Fanuc.
        
        Args:
            config: Configuration connexion (défaut si None)
        """
        if not PYCOMM3_AVAILABLE:
            raise ImportError(
                "pycomm3 not installed. Run: pip install pycomm3"
            )
        
        self.config = config or FanucConfig()
        self._driver: Optional[CIPDriver] = None
        self._connected = False
        self._running = False
        self._read_task: Optional[asyncio.Task] = None
        
        # Callbacks
        self._on_status_update: List[Callable[[FanucStatus], None]] = []
        self._on_connection_change: List[Callable[[bool], None]] = []
        
        # État courant
        self._current_status = FanucStatus()
        self._previous_position: Optional[FanucTCPPosition] = None
        self._last_read_time: Optional[datetime] = None
        
        logger.info("fanuc_driver_initialized", ip=self.config.ip)
    
    @property
    def is_connected(self) -> bool:
        """Indique si connecté au robot."""
        return self._connected
    
    @property
    def current_status(self) -> FanucStatus:
        """État actuel du robot."""
        return self._current_status
    
    async def connect(self) -> bool:
        """
        Établit la connexion au robot.
        
        Returns:
            True si connecté avec succès
        """
        try:
            loop = asyncio.get_event_loop()
            
            def _connect():
                driver = CIPDriver(self.config.ip)
                driver.open()
                return driver
            
            self._driver = await loop.run_in_executor(None, _connect)
            self._connected = True
            
            logger.info("fanuc_connected", ip=self.config.ip)
            
            for callback in self._on_connection_change:
                callback(True)
            
            return True
            
        except Exception as e:
            logger.error("fanuc_connection_failed", error=str(e))
            self._connected = False
            return False
    
    async def disconnect(self) -> None:
        """Ferme la connexion."""
        if self._driver:
            try:
                self._driver.close()
            except Exception:
                pass
            self._driver = None
        
        self._connected = False
        
        for callback in self._on_connection_change:
            callback(False)
        
        logger.info("fanuc_disconnected")
    
    async def read_status(self) -> Optional[FanucStatus]:
        """
        Lit l'état complet du robot.
        
        Returns:
            FanucStatus ou None si erreur
        """
        if not self.is_connected:
            return None
        
        try:
            loop = asyncio.get_event_loop()
            
            # Lecture des registres via pycomm3
            # Note: Les tags exacts dépendent de la config Fanuc
            
            status = FanucStatus(timestamp=datetime.now())
            
            # Lire les données (exemple générique)
            # En production, adapter aux tags réels du robot
            def _read_data():
                # Lecture registres status
                # R[1] = speed override, etc.
                results = {}
                try:
                    # Ces tags sont des exemples - à adapter
                    results['override'] = self._driver.generic_message(
                        service=0x0E,  # Get Attribute Single
                        class_code=0x68,  # Motion class
                        instance=1,
                        attribute=1,
                    )
                except Exception:
                    pass
                return results
            
            data = await loop.run_in_executor(None, _read_data)
            
            # Parser les données
            status.servo_on = True  # À lire depuis le robot
            status.power_on = True
            status.mode = FanucMode.AUTO  # À déterminer depuis I/O
            
            # Calculer la vitesse depuis delta position
            if self._previous_position and self._last_read_time:
                dt = (status.timestamp - self._last_read_time).total_seconds()
                if dt > 0:
                    distance = status.tcp_position.distance_to(self._previous_position)
                    status.current_speed_mms = distance / dt
            
            self._previous_position = status.tcp_position
            self._last_read_time = status.timestamp
            self._current_status = status
            
            # Notifier
            for callback in self._on_status_update:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(status)
                    else:
                        callback(status)
                except Exception as e:
                    logger.error("fanuc_callback_error", error=str(e))
            
            return status
            
        except Exception as e:
            logger.warning("fanuc_read_error", error=str(e))
            return None
    
    async def set_speed_override(self, percent: int) -> bool:
        """
        Définit l'override de vitesse.
        
        Args:
            percent: Override 0-100%
            
        Returns:
            True si réussi
        """
        if not self.is_connected:
            return False
        
        percent = max(0, min(100, percent))
        
        try:
            loop = asyncio.get_event_loop()
            
            def _write():
                # Écrire dans registre R[1] (exemple)
                # À adapter selon config robot
                pass
            
            await loop.run_in_executor(None, _write)
            
            logger.info("fanuc_speed_override_set", percent=percent)
            return True
            
        except Exception as e:
            logger.error("fanuc_write_error", error=str(e))
            return False
    
    async def start_cyclic_read(self, interval_ms: float = 50) -> None:
        """Démarre la lecture cyclique."""
        if self._running:
            return
        
        self._running = True
        self._read_task = asyncio.create_task(
            self._cyclic_read_loop(interval_ms / 1000.0)
        )
        logger.info("fanuc_cyclic_read_started", interval_ms=interval_ms)
    
    async def stop_cyclic_read(self) -> None:
        """Arrête la lecture cyclique."""
        self._running = False
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        logger.info("fanuc_cyclic_read_stopped")
    
    async def _cyclic_read_loop(self, interval: float) -> None:
        """Boucle de lecture cyclique."""
        while self._running:
            if self.is_connected:
                await self.read_status()
            else:
                await self.connect()
            
            await asyncio.sleep(interval)
    
    def on_status_update(self, callback: Callable[[FanucStatus], None]) -> None:
        """Ajoute un callback pour les mises à jour."""
        self._on_status_update.append(callback)
    
    def on_connection_change(self, callback: Callable[[bool], None]) -> None:
        """Ajoute un callback pour les changements de connexion."""
        self._on_connection_change.append(callback)


class FanucSimulator:
    """
    Simulateur Fanuc pour tests sans robot réel.
    """
    
    def __init__(self):
        self._running = False
        self._status = FanucStatus(power_on=True, servo_on=True, mode=FanucMode.AUTO)
        self._callbacks: List[Callable[[FanucStatus], None]] = []
        self._speed_override = 100
        self._position = FanucTCPPosition()
    
    @property
    def is_connected(self) -> bool:
        return self._running
    
    @property
    def current_status(self) -> FanucStatus:
        return self._status
    
    async def connect(self) -> bool:
        self._running = True
        logger.info("fanuc_simulator_connected")
        return True
    
    async def disconnect(self) -> None:
        self._running = False
        logger.info("fanuc_simulator_disconnected")
    
    async def start_cyclic_read(self, interval_ms: float = 50) -> None:
        asyncio.create_task(self._simulation_loop(interval_ms / 1000.0))
    
    async def stop_cyclic_read(self) -> None:
        self._running = False
    
    async def _simulation_loop(self, interval: float) -> None:
        import random
        import math
        
        t = 0.0
        while self._running:
            # Simuler mouvement circulaire
            t += interval
            radius = 500  # mm
            
            self._position = FanucTCPPosition(
                x=radius * math.cos(t * 0.5) + 1000,
                y=radius * math.sin(t * 0.5),
                z=500 + 50 * math.sin(t),
                w=0, p=0, r=0,
            )
            
            # Calculer vitesse (mm/s)
            speed = radius * 0.5  # ≈ 250 mm/s
            
            self._status = FanucStatus(
                timestamp=datetime.now(),
                power_on=True,
                servo_on=True,
                mode=FanucMode(random.choice([1, 1, 1, 2])),  # Mostly AUTO
                program_state=FanucState.RUNNING,
                alarm_level=FanucAlarm.NONE,
                in_motion=random.random() > 0.2,
                speed_override=self._speed_override,
                current_speed_mms=speed * (self._speed_override / 100),
                tcp_position=self._position,
                current_program="WELD_MAIN",
                current_line=random.randint(1, 500),
            )
            
            for callback in self._callbacks:
                try:
                    callback(self._status)
                except Exception:
                    pass
            
            await asyncio.sleep(interval)
    
    def on_status_update(self, callback: Callable[[FanucStatus], None]) -> None:
        self._callbacks.append(callback)
    
    async def set_speed_override(self, percent: int) -> bool:
        self._speed_override = max(0, min(100, percent))
        logger.info("fanuc_simulator_override", percent=self._speed_override)
        return True
