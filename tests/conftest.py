"""
Configuration pytest pour RoboSafe Sentinel.
"""

import asyncio
import pytest


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sample_signal_values():
    """Valeurs de signaux pour tests."""
    return {
        "fanuc_tcp_speed": 250.0,
        "fanuc_mode": "AUTO",
        "fanuc_servo_on": True,
        "plc_heartbeat": 12345,
        "scanner_zone_status": 0x00,
        "scanner_min_distance": 2500,
        "estop_status": 0,
        "arc_on": True,
        "fumes_concentration": 3.5,
        "fumes_vlep_ratio": 0.7,
        "vision_presence": False,
        "vision_min_distance": 3000,
        "vision_confidence": 0.92,
        "robosafe_risk_score": 25.0,
    }


@pytest.fixture
def hazardous_signal_values():
    """Valeurs de signaux simulant une situation dangereuse."""
    return {
        "fanuc_tcp_speed": 500.0,
        "fanuc_mode": "AUTO",
        "fanuc_servo_on": True,
        "plc_heartbeat": 12345,
        "scanner_zone_status": 0x04,  # PROTECT zone
        "scanner_min_distance": 400,
        "estop_status": 0,
        "arc_on": True,
        "fumes_concentration": 7.0,
        "fumes_vlep_ratio": 1.4,  # > 120% VLEP
        "vision_presence": True,
        "vision_min_distance": 600,
        "vision_confidence": 0.88,
        "robosafe_risk_score": 85.0,
    }
