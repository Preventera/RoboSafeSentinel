"""
Drivers capteurs RoboSafe Sentinel.

Ce module contient les drivers de communication pour:
- PLC Siemens S7-1500F (sécurité)
- Robot Fanuc (EtherNet/IP)
- Scanner SICK microScan3
- Capteur fumées (Modbus TCP)
- Vision IA (à venir)

Chaque driver dispose d'une version Simulator pour les tests.
"""

from robosafe.sensors.plc_siemens import (
    SiemensS7Driver,
    SiemensS7Simulator,
    S7Config,
    SafetyStatus,
    SafetyCommand,
    ScannerZone,
)

from robosafe.sensors.robot_fanuc import (
    FanucDriver,
    FanucSimulator,
    FanucConfig,
    FanucStatus,
    FanucMode,
    FanucTCPPosition,
)

from robosafe.sensors.scanner_sick import (
    SICKScannerDriver,
    SICKScannerSimulator,
    ScannerConfig,
    ScannerMeasurement,
    ScannerZone as SICKZone,
)

from robosafe.sensors.fumes_sensor import (
    FumesSensorDriver,
    FumesSensorSimulator,
    FumesConfig,
    FumesMeasurement,
    FumesAlertLevel,
)

from robosafe.sensors.vision_ai import (
    VisionAIDriver,
    VisionSimulator,
    VisionConfig,
    VisionResult,
    DetectedPerson,
    PPEType,
    PostureRisk,
)

__all__ = [
    # Siemens S7
    "SiemensS7Driver",
    "SiemensS7Simulator",
    "S7Config",
    "SafetyStatus",
    "SafetyCommand",
    
    # Fanuc
    "FanucDriver",
    "FanucSimulator",
    "FanucConfig",
    "FanucStatus",
    "FanucMode",
    "FanucTCPPosition",
    
    # SICK Scanner
    "SICKScannerDriver",
    "SICKScannerSimulator",
    "ScannerConfig",
    "ScannerMeasurement",
    "ScannerZone",
    "SICKZone",
    
    # Fumes
    "FumesSensorDriver",
    "FumesSensorSimulator",
    "FumesConfig",
    "FumesMeasurement",
    "FumesAlertLevel",
    
    # Vision AI
    "VisionAIDriver",
    "VisionSimulator",
    "VisionConfig",
    "VisionResult",
    "DetectedPerson",
    "PPEType",
    "PostureRisk",
]
