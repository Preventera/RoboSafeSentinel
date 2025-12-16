"""
Driver Siemens S7-1500F pour RoboSafe Sentinel.

Communication avec le PLC Safety via Snap7 (protocole S7).
Gère les signaux de sécurité PROFIsafe et les commandes STOP/SLOW.

Requires:
    pip install python-snap7

Note:
    Pour S7-1500F avec PROFIsafe, certaines données de sécurité
    ne sont accessibles qu'en lecture. Les commandes passent par
    des DB partagés non-safety.
"""

import asyncio
import struct
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum, IntFlag
from typing import Any, Callable, Dict, List, Optional, Tuple
import structlog

try:
    import snap7
    from snap7.util import get_bool, set_bool, get_int, get_real
    SNAP7_AVAILABLE = True
except ImportError:
    SNAP7_AVAILABLE = False
    snap7 = None

logger = structlog.get_logger(__name__)


class ScannerZone(IntFlag):
    """Zones scanner SICK (bitfield)."""
    CLEAR = 0x00
    MONITORING = 0x01
    WARNING = 0x02
    PROTECTIVE = 0x04


class RobotMode(IntEnum):
    """Modes robot Fanuc."""
    UNKNOWN = 0
    AUTO = 1
    T1 = 2  # Teach 1 (low speed)
    T2 = 3  # Teach 2 (high speed)


class SafetyCommand(IntEnum):
    """Commandes sécurité vers PLC."""
    NONE = 0
    SLOW_50 = 1
    SLOW_25 = 2
    STOP_CAT1 = 3
    ESTOP = 4
    RESET = 5


@dataclass
class PLCDataBlock:
    """Définition d'un bloc de données PLC."""
    db_number: int
    offset: int
    size: int
    description: str = ""


@dataclass
class S7Config:
    """Configuration connexion S7."""
    ip: str = "192.168.1.20"
    rack: int = 0
    slot: int = 1
    
    # Data Blocks
    db_safety_status: int = 100   # DB lecture états sécurité
    db_robot_status: int = 101    # DB lecture états robot
    db_robosafe_cmd: int = 200    # DB écriture commandes RoboSafe
    db_robosafe_hb: int = 201     # DB heartbeat RoboSafe
    
    # Timeouts
    connect_timeout_ms: int = 5000
    read_timeout_ms: int = 100
    heartbeat_interval_ms: int = 100


@dataclass
class SafetyStatus:
    """État de sécurité lu depuis le PLC."""
    timestamp: datetime = field(default_factory=datetime.now)
    
    # États globaux
    plc_run: bool = False
    safety_ok: bool = False
    estop_active: bool = False
    
    # Scanners
    scanner1_zone: ScannerZone = ScannerZone.CLEAR
    scanner2_zone: ScannerZone = ScannerZone.CLEAR
    scanner_min_distance_mm: int = 0
    
    # Barrière immatérielle
    light_curtain_clear: bool = True
    
    # Interlock porte
    door_closed: bool = True
    door_locked: bool = True
    
    # Robot
    robot_mode: RobotMode = RobotMode.UNKNOWN
    robot_in_motion: bool = False
    robot_speed_percent: int = 0
    
    # Heartbeats
    plc_heartbeat: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire pour SignalManager."""
        return {
            "plc_run": self.plc_run,
            "safety_ok": self.safety_ok,
            "estop_status": 1 if self.estop_active else 0,
            "scanner1_zone": self.scanner1_zone.value,
            "scanner2_zone": self.scanner2_zone.value,
            "scanner_zone_status": self.scanner1_zone.value | self.scanner2_zone.value,
            "scanner_min_distance": self.scanner_min_distance_mm,
            "light_curtain_clear": self.light_curtain_clear,
            "door_closed": self.door_closed,
            "door_locked": self.door_locked,
            "robot_mode": self.robot_mode.name,
            "robot_in_motion": self.robot_in_motion,
            "robot_speed_percent": self.robot_speed_percent,
            "plc_heartbeat": self.plc_heartbeat,
        }


class SiemensS7Driver:
    """
    Driver de communication Siemens S7-1500F.
    
    Fonctionnalités:
    - Lecture cyclique des états de sécurité
    - Envoi de commandes SLOW/STOP
    - Heartbeat bidirectionnel
    - Reconnexion automatique
    
    Architecture PLC typique:
    - DB100: Safety Status (lecture seule)
      - Byte 0: États globaux (PLC_RUN, SAFETY_OK, ESTOP)
      - Byte 1-2: Scanner 1 zone + distance
      - Byte 3-4: Scanner 2 zone + distance
      - Byte 5: Barrière, porte
      - Byte 6-7: Robot mode, motion, speed
      - Byte 8-9: Heartbeat PLC
    
    - DB200: RoboSafe Commands (écriture)
      - Byte 0: Commande active
      - Byte 1: Paramètre (vitesse %)
      - Byte 2-3: Heartbeat RoboSafe
    """
    
    def __init__(self, config: Optional[S7Config] = None):
        """
        Initialise le driver S7.
        
        Args:
            config: Configuration connexion (défaut si None)
        """
        if not SNAP7_AVAILABLE:
            raise ImportError(
                "python-snap7 not installed. "
                "Run: pip install python-snap7"
            )
        
        self.config = config or S7Config()
        self._client: Optional[snap7.client.Client] = None
        self._connected = False
        self._running = False
        self._read_task: Optional[asyncio.Task] = None
        self._heartbeat_counter = 0
        
        # Callbacks
        self._on_status_update: List[Callable[[SafetyStatus], None]] = []
        self._on_connection_change: List[Callable[[bool], None]] = []
        
        # État courant
        self._current_status = SafetyStatus()
        self._last_read_time: Optional[datetime] = None
        self._read_errors = 0
        
        logger.info(
            "s7_driver_initialized",
            ip=self.config.ip,
            rack=self.config.rack,
            slot=self.config.slot,
        )
    
    @property
    def is_connected(self) -> bool:
        """Indique si connecté au PLC."""
        return self._connected and self._client is not None
    
    @property
    def current_status(self) -> SafetyStatus:
        """État de sécurité actuel."""
        return self._current_status
    
    async def connect(self) -> bool:
        """
        Établit la connexion au PLC.
        
        Returns:
            True si connecté avec succès
        """
        try:
            self._client = snap7.client.Client()
            self._client.set_connection_params(
                self.config.ip,
                self.config.rack,
                self.config.slot,
            )
            
            # Connexion (bloquante, donc dans executor)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self._client.connect(
                    self.config.ip,
                    self.config.rack,
                    self.config.slot,
                )
            )
            
            self._connected = True
            self._read_errors = 0
            
            logger.info(
                "s7_connected",
                ip=self.config.ip,
                cpu_info=self._get_cpu_info(),
            )
            
            # Notifier
            for callback in self._on_connection_change:
                callback(True)
            
            return True
            
        except Exception as e:
            logger.error("s7_connection_failed", error=str(e))
            self._connected = False
            return False
    
    async def disconnect(self) -> None:
        """Ferme la connexion."""
        if self._client:
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
        
        self._connected = False
        
        for callback in self._on_connection_change:
            callback(False)
        
        logger.info("s7_disconnected")
    
    def _get_cpu_info(self) -> Dict[str, str]:
        """Récupère les infos CPU (si connecté)."""
        if not self._client:
            return {}
        try:
            info = self._client.get_cpu_info()
            return {
                "module_type": info.ModuleTypeName.decode(),
                "serial": info.SerialNumber.decode(),
                "name": info.ASName.decode(),
            }
        except Exception:
            return {}
    
    async def read_safety_status(self) -> Optional[SafetyStatus]:
        """
        Lit l'état de sécurité depuis le PLC.
        
        Returns:
            SafetyStatus ou None si erreur
        """
        if not self.is_connected:
            return None
        
        try:
            loop = asyncio.get_event_loop()
            
            # Lire DB Safety Status (10 bytes)
            data = await loop.run_in_executor(
                None,
                lambda: self._client.db_read(
                    self.config.db_safety_status, 0, 10
                )
            )
            
            status = self._parse_safety_status(data)
            self._current_status = status
            self._last_read_time = datetime.now()
            self._read_errors = 0
            
            # Notifier
            for callback in self._on_status_update:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(status)
                    else:
                        callback(status)
                except Exception as e:
                    logger.error("status_callback_error", error=str(e))
            
            return status
            
        except Exception as e:
            self._read_errors += 1
            logger.warning(
                "s7_read_error",
                error=str(e),
                error_count=self._read_errors,
            )
            
            if self._read_errors >= 5:
                logger.error("s7_too_many_errors", reconnecting=True)
                await self.disconnect()
                await asyncio.sleep(1)
                await self.connect()
            
            return None
    
    def _parse_safety_status(self, data: bytes) -> SafetyStatus:
        """
        Parse les données brutes du DB Safety.
        
        Format attendu (10 bytes):
        - Byte 0: Bit0=PLC_RUN, Bit1=SAFETY_OK, Bit2=ESTOP
        - Byte 1: Scanner1 zone (0=clear, 1=mon, 2=warn, 4=protect)
        - Byte 2-3: Scanner1 distance min (uint16, mm)
        - Byte 4: Scanner2 zone
        - Byte 5: Bit0=LightCurtain, Bit1=DoorClosed, Bit2=DoorLocked
        - Byte 6: Robot mode (1=AUTO, 2=T1, 3=T2)
        - Byte 7: Robot speed %
        - Byte 8-9: Heartbeat PLC (uint16)
        """
        status = SafetyStatus()
        
        if len(data) < 10:
            return status
        
        # Byte 0: États globaux
        status.plc_run = bool(data[0] & 0x01)
        status.safety_ok = bool(data[0] & 0x02)
        status.estop_active = bool(data[0] & 0x04)
        
        # Byte 1-3: Scanner 1
        status.scanner1_zone = ScannerZone(data[1] & 0x07)
        dist1 = struct.unpack(">H", data[2:4])[0]
        
        # Byte 4: Scanner 2
        status.scanner2_zone = ScannerZone(data[4] & 0x07)
        
        # Distance min = min des deux scanners
        status.scanner_min_distance_mm = dist1  # Simplifié
        
        # Byte 5: Barrière et porte
        status.light_curtain_clear = bool(data[5] & 0x01)
        status.door_closed = bool(data[5] & 0x02)
        status.door_locked = bool(data[5] & 0x04)
        
        # Byte 6-7: Robot
        status.robot_mode = RobotMode(data[6]) if data[6] in [1, 2, 3] else RobotMode.UNKNOWN
        status.robot_speed_percent = data[7]
        status.robot_in_motion = status.robot_speed_percent > 0
        
        # Byte 8-9: Heartbeat
        status.plc_heartbeat = struct.unpack(">H", data[8:10])[0]
        
        return status
    
    async def send_command(self, command: SafetyCommand, param: int = 0) -> bool:
        """
        Envoie une commande au PLC.
        
        Args:
            command: Commande à envoyer
            param: Paramètre (ex: vitesse %)
            
        Returns:
            True si envoyé avec succès
        """
        if not self.is_connected:
            return False
        
        try:
            # Préparer les données (4 bytes)
            # Byte 0: Commande
            # Byte 1: Paramètre
            # Byte 2-3: Heartbeat RoboSafe
            self._heartbeat_counter = (self._heartbeat_counter + 1) % 65536
            
            data = bytearray(4)
            data[0] = command.value
            data[1] = param
            struct.pack_into(">H", data, 2, self._heartbeat_counter)
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self._client.db_write(
                    self.config.db_robosafe_cmd, 0, data
                )
            )
            
            logger.info(
                "s7_command_sent",
                command=command.name,
                param=param,
                heartbeat=self._heartbeat_counter,
            )
            
            return True
            
        except Exception as e:
            logger.error("s7_command_error", error=str(e))
            return False
    
    async def request_slow(self, speed_percent: int) -> bool:
        """Demande un ralentissement."""
        cmd = SafetyCommand.SLOW_25 if speed_percent <= 25 else SafetyCommand.SLOW_50
        return await self.send_command(cmd, speed_percent)
    
    async def request_stop(self) -> bool:
        """Demande un arrêt CAT.1."""
        return await self.send_command(SafetyCommand.STOP_CAT1)
    
    async def request_estop(self) -> bool:
        """Demande un E-STOP (CAT.0)."""
        return await self.send_command(SafetyCommand.ESTOP)
    
    async def clear_command(self) -> bool:
        """Efface la commande active."""
        return await self.send_command(SafetyCommand.NONE)
    
    async def start_cyclic_read(self, interval_ms: float = 100) -> None:
        """
        Démarre la lecture cyclique.
        
        Args:
            interval_ms: Intervalle de lecture en ms
        """
        if self._running:
            return
        
        self._running = True
        self._read_task = asyncio.create_task(
            self._cyclic_read_loop(interval_ms / 1000.0)
        )
        logger.info("s7_cyclic_read_started", interval_ms=interval_ms)
    
    async def stop_cyclic_read(self) -> None:
        """Arrête la lecture cyclique."""
        self._running = False
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        logger.info("s7_cyclic_read_stopped")
    
    async def _cyclic_read_loop(self, interval: float) -> None:
        """Boucle de lecture cyclique."""
        while self._running:
            if self.is_connected:
                await self.read_safety_status()
                await self._send_heartbeat()
            else:
                await self.connect()
            
            await asyncio.sleep(interval)
    
    async def _send_heartbeat(self) -> None:
        """Envoie le heartbeat RoboSafe."""
        if not self.is_connected:
            return
        
        try:
            self._heartbeat_counter = (self._heartbeat_counter + 1) % 65536
            
            data = bytearray(2)
            struct.pack_into(">H", data, 0, self._heartbeat_counter)
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self._client.db_write(
                    self.config.db_robosafe_hb, 0, data
                )
            )
        except Exception:
            pass  # Heartbeat failure handled by PLC timeout
    
    def on_status_update(self, callback: Callable[[SafetyStatus], None]) -> None:
        """Ajoute un callback pour les mises à jour de statut."""
        self._on_status_update.append(callback)
    
    def on_connection_change(self, callback: Callable[[bool], None]) -> None:
        """Ajoute un callback pour les changements de connexion."""
        self._on_connection_change.append(callback)


class SiemensS7Simulator:
    """
    Simulateur S7 pour tests sans PLC réel.
    
    Génère des données réalistes pour le développement.
    """
    
    def __init__(self):
        self._running = False
        self._status = SafetyStatus(plc_run=True, safety_ok=True)
        self._callbacks: List[Callable[[SafetyStatus], None]] = []
        self._heartbeat = 0
    
    @property
    def is_connected(self) -> bool:
        return self._running
    
    @property
    def current_status(self) -> SafetyStatus:
        return self._status
    
    async def connect(self) -> bool:
        self._running = True
        logger.info("s7_simulator_connected")
        return True
    
    async def disconnect(self) -> None:
        self._running = False
        logger.info("s7_simulator_disconnected")
    
    async def start_cyclic_read(self, interval_ms: float = 100) -> None:
        asyncio.create_task(self._simulation_loop(interval_ms / 1000.0))
    
    async def stop_cyclic_read(self) -> None:
        self._running = False
    
    async def _simulation_loop(self, interval: float) -> None:
        import random
        
        while self._running:
            self._heartbeat = (self._heartbeat + 1) % 65536
            
            # Simuler des valeurs réalistes
            self._status = SafetyStatus(
                plc_run=True,
                safety_ok=random.random() > 0.05,
                estop_active=random.random() < 0.02,
                scanner1_zone=ScannerZone(random.choice([0, 0, 0, 1, 2, 4])),
                scanner2_zone=ScannerZone(random.choice([0, 0, 0, 1, 2, 4])),
                scanner_min_distance_mm=random.randint(500, 5000),
                light_curtain_clear=random.random() > 0.1,
                door_closed=True,
                door_locked=True,
                robot_mode=RobotMode.AUTO,
                robot_in_motion=random.random() > 0.3,
                robot_speed_percent=random.randint(0, 100),
                plc_heartbeat=self._heartbeat,
            )
            
            for callback in self._callbacks:
                try:
                    callback(self._status)
                except Exception:
                    pass
            
            await asyncio.sleep(interval)
    
    def on_status_update(self, callback: Callable[[SafetyStatus], None]) -> None:
        self._callbacks.append(callback)
    
    async def send_command(self, command: SafetyCommand, param: int = 0) -> bool:
        logger.info("s7_simulator_command", command=command.name, param=param)
        return True
    
    async def request_stop(self) -> bool:
        return await self.send_command(SafetyCommand.STOP_CAT1)
    
    async def request_estop(self) -> bool:
        return await self.send_command(SafetyCommand.ESTOP)
    
    async def request_slow(self, speed: int) -> bool:
        return await self.send_command(SafetyCommand.SLOW_50, speed)
