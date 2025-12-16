"""
Gestion de la configuration RoboSafe.

Charge et valide la configuration depuis fichiers YAML.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings


class CellConfig(BaseModel):
    """Configuration de la cellule."""
    id: str = "CELL-001"
    name: str = "Cellule robotisée"
    type: str = "generic"  # welding, assembly, palletizing, etc.


class RobotConfig(BaseModel):
    """Configuration du robot."""
    type: str = "fanuc"
    model: str = "ARC Mate 100iD"
    ip: str = "192.168.1.10"
    protocol: str = "ethernet_ip"
    payload_kg: float = 12.0
    reach_mm: float = 1420.0


class PLCConfig(BaseModel):
    """Configuration du PLC sécurité."""
    type: str = "siemens"
    model: str = "S7-1500F"
    ip: str = "192.168.1.20"
    protocol: str = "profisafe"
    rack: int = 0
    slot: int = 1


class ScannerConfig(BaseModel):
    """Configuration d'un scanner laser."""
    id: str
    type: str = "sick_microscan3"
    ip: str = "192.168.1.30"
    zones: Dict[str, int] = Field(default_factory=lambda: {
        "warn": 1200,
        "protect": 500,
    })


class VisionConfig(BaseModel):
    """Configuration de la vision IA."""
    enabled: bool = True
    ip: str = "192.168.1.40"
    model: str = "basler_ace2"
    protocol: str = "gige_vision"
    fps: int = 30
    resolution: str = "1920x1080"


class FumesConfig(BaseModel):
    """Configuration du capteur fumées."""
    enabled: bool = True
    ip: str = "192.168.1.50"
    protocol: str = "modbus_tcp"
    port: int = 502
    vlep: float = 5.0  # mg/m³
    
    # Seuils relatifs (% VLEP)
    warning_ratio: float = 0.5
    alert_ratio: float = 0.8
    critical_ratio: float = 1.0
    stop_ratio: float = 1.2


class ThresholdsConfig(BaseModel):
    """Seuils de sécurité."""
    
    # Distance (mm)
    distance_stop: int = 800
    distance_slow: int = 1500
    distance_warn: int = 2000
    
    # Temps (ms)
    plc_heartbeat_timeout: int = 500
    vision_timeout: int = 500
    fumes_timeout: int = 5000
    
    # Marges
    safety_margin_percent: int = 20


class LoggingConfig(BaseModel):
    """Configuration du logging."""
    level: str = "INFO"
    format: str = "json"  # json, console
    output: str = "logs/robosafe.log"
    max_size_mb: int = 100
    backup_count: int = 10


class APIConfig(BaseModel):
    """Configuration de l'API."""
    host: str = "0.0.0.0"
    port: int = 8080
    cors_origins: List[str] = Field(default_factory=lambda: ["*"])


class RoboSafeConfig(BaseSettings):
    """Configuration complète RoboSafe."""
    
    cell: CellConfig = Field(default_factory=CellConfig)
    robot: RobotConfig = Field(default_factory=RobotConfig)
    plc: PLCConfig = Field(default_factory=PLCConfig)
    scanners: List[ScannerConfig] = Field(default_factory=list)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    fumes: FumesConfig = Field(default_factory=FumesConfig)
    thresholds: ThresholdsConfig = Field(default_factory=ThresholdsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    
    class Config:
        env_prefix = "ROBOSAFE_"
        env_nested_delimiter = "__"


def load_config(path: Path) -> RoboSafeConfig:
    """
    Charge la configuration depuis un fichier YAML.
    
    Args:
        path: Chemin vers le fichier YAML
        
    Returns:
        Configuration validée
    """
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    
    return RoboSafeConfig(**data)


def save_config(config: RoboSafeConfig, path: Path) -> None:
    """
    Sauvegarde la configuration dans un fichier YAML.
    
    Args:
        config: Configuration à sauvegarder
        path: Chemin de destination
    """
    data = config.model_dump()
    
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def create_example_config(path: Path) -> None:
    """Crée un fichier de configuration exemple."""
    config = RoboSafeConfig(
        cell=CellConfig(
            id="WELD-MIG-001",
            name="Cellule Soudage MIG",
            type="welding",
        ),
        scanners=[
            ScannerConfig(id="scanner_left", ip="192.168.1.30"),
            ScannerConfig(id="scanner_right", ip="192.168.1.31"),
        ],
    )
    
    save_config(config, path)
