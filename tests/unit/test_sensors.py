"""
Tests unitaires pour les drivers capteurs.
"""

import pytest
from datetime import datetime

from robosafe.sensors.plc_siemens import (
    SiemensS7Simulator,
    SafetyStatus,
    SafetyCommand,
    ScannerZone,
    RobotMode,
)
from robosafe.sensors.robot_fanuc import (
    FanucSimulator,
    FanucStatus,
    FanucMode,
    FanucTCPPosition,
)
from robosafe.sensors.scanner_sick import (
    SICKScannerSimulator,
    ScannerConfig,
    ScannerMeasurement,
    ScannerZone as SICKZone,
)
from robosafe.sensors.fumes_sensor import (
    FumesSensorSimulator,
    FumesConfig,
    FumesMeasurement,
    FumesAlertLevel,
)


class TestSiemensS7Simulator:
    """Tests pour le simulateur Siemens S7."""
    
    @pytest.fixture
    def simulator(self):
        return SiemensS7Simulator()
    
    @pytest.mark.asyncio
    async def test_connect(self, simulator):
        """Test connexion simulateur."""
        result = await simulator.connect()
        assert result is True
        assert simulator.is_connected is True
    
    @pytest.mark.asyncio
    async def test_disconnect(self, simulator):
        """Test déconnexion."""
        await simulator.connect()
        await simulator.disconnect()
        assert simulator.is_connected is False
    
    @pytest.mark.asyncio
    async def test_send_command(self, simulator):
        """Test envoi commande."""
        await simulator.connect()
        result = await simulator.send_command(SafetyCommand.SLOW_50, 50)
        assert result is True
    
    @pytest.mark.asyncio
    async def test_request_stop(self, simulator):
        """Test demande arrêt."""
        await simulator.connect()
        result = await simulator.request_stop()
        assert result is True
    
    def test_safety_status_to_dict(self):
        """Test conversion SafetyStatus en dict."""
        status = SafetyStatus(
            plc_run=True,
            safety_ok=True,
            scanner1_zone=ScannerZone.WARNING,
            scanner_min_distance_mm=1000,
        )
        
        d = status.to_dict()
        
        assert d["plc_run"] is True
        assert d["safety_ok"] is True
        assert d["scanner_min_distance"] == 1000
        assert d["scanner1_zone"] == ScannerZone.WARNING.value
    
    def test_scanner_zone_flags(self):
        """Test flags zone scanner."""
        assert ScannerZone.CLEAR.value == 0
        assert ScannerZone.WARNING.value == 2
        assert ScannerZone.PROTECTIVE.value == 4
        
        # Combinaison
        combined = ScannerZone.WARNING | ScannerZone.PROTECTIVE
        assert combined & ScannerZone.WARNING
        assert combined & ScannerZone.PROTECTIVE


class TestFanucSimulator:
    """Tests pour le simulateur Fanuc."""
    
    @pytest.fixture
    def simulator(self):
        return FanucSimulator()
    
    @pytest.mark.asyncio
    async def test_connect(self, simulator):
        """Test connexion."""
        result = await simulator.connect()
        assert result is True
        assert simulator.is_connected is True
    
    @pytest.mark.asyncio
    async def test_set_speed_override(self, simulator):
        """Test modification override vitesse."""
        await simulator.connect()
        result = await simulator.set_speed_override(50)
        assert result is True
    
    def test_tcp_position_distance(self):
        """Test calcul distance TCP."""
        pos1 = FanucTCPPosition(x=0, y=0, z=0)
        pos2 = FanucTCPPosition(x=100, y=0, z=0)
        
        distance = pos1.distance_to(pos2)
        assert distance == 100.0
        
        pos3 = FanucTCPPosition(x=300, y=400, z=0)
        distance = pos1.distance_to(pos3)
        assert distance == 500.0  # 3-4-5 triangle
    
    def test_fanuc_status_to_dict(self):
        """Test conversion FanucStatus en dict."""
        status = FanucStatus(
            servo_on=True,
            mode=FanucMode.AUTO,
            current_speed_mms=250.0,
        )
        
        d = status.to_dict()
        
        assert d["fanuc_servo_on"] is True
        assert d["fanuc_mode"] == "AUTO"
        assert d["fanuc_tcp_speed"] == 250.0


class TestSICKScannerSimulator:
    """Tests pour le simulateur scanner SICK."""
    
    @pytest.fixture
    def config(self):
        return ScannerConfig(
            id="scanner_test",
            zone_protective_mm=500,
            zone_warning_mm=1200,
        )
    
    @pytest.fixture
    def simulator(self, config):
        return SICKScannerSimulator(config)
    
    @pytest.mark.asyncio
    async def test_connect(self, simulator):
        """Test connexion."""
        result = await simulator.connect()
        assert result is True
    
    def test_zone_requires_stop(self):
        """Test zone requiert arrêt."""
        assert SICKZone.PROTECTIVE.requires_stop is True
        assert SICKZone.WARNING.requires_stop is False
        assert SICKZone.CLEAR.requires_stop is False
    
    def test_zone_requires_slow(self):
        """Test zone requiert ralentissement."""
        assert SICKZone.WARNING.requires_slow is True
        assert SICKZone.PROTECTIVE.requires_slow is False
        assert SICKZone.CLEAR.requires_slow is False
    
    def test_measurement_to_dict(self, config):
        """Test conversion mesure en dict."""
        measurement = ScannerMeasurement(
            scanner_id="scanner_1",
            active_zone=SICKZone.WARNING,
            min_distance_mm=800,
        )
        
        d = measurement.to_dict()
        
        assert d["scanner_1_zone"] == SICKZone.WARNING.value
        assert d["scanner_1_min_distance"] == 800
        assert d["scanner_1_requires_slow"] is True


class TestFumesSensorSimulator:
    """Tests pour le simulateur capteur fumées."""
    
    @pytest.fixture
    def config(self):
        return FumesConfig(
            vlep_mgm3=5.0,
            threshold_yellow=0.5,
            threshold_orange=0.8,
            threshold_red=1.0,
            threshold_critical=1.2,
        )
    
    @pytest.fixture
    def simulator(self, config):
        return FumesSensorSimulator(config)
    
    @pytest.mark.asyncio
    async def test_connect(self, simulator):
        """Test connexion."""
        result = await simulator.connect()
        assert result is True
    
    def test_alert_levels(self, config):
        """Test niveaux d'alerte."""
        # < 50% = GREEN
        m = FumesMeasurement(vlep_ratio=0.3, alert_level=FumesAlertLevel.GREEN)
        assert not m.requires_alert
        assert not m.requires_slow
        assert not m.requires_stop
        
        # 80-100% = ORANGE
        m = FumesMeasurement(vlep_ratio=0.9, alert_level=FumesAlertLevel.ORANGE)
        assert m.requires_alert
        assert not m.requires_slow
        assert not m.requires_stop
        
        # 100-120% = RED
        m = FumesMeasurement(vlep_ratio=1.1, alert_level=FumesAlertLevel.RED)
        assert m.requires_alert
        assert m.requires_slow
        assert not m.requires_stop
        
        # > 120% = CRITICAL
        m = FumesMeasurement(vlep_ratio=1.3, alert_level=FumesAlertLevel.CRITICAL)
        assert m.requires_alert
        assert not m.requires_slow  # Pas slow, mais stop
        assert m.requires_stop
    
    def test_measurement_to_dict(self):
        """Test conversion mesure en dict."""
        m = FumesMeasurement(
            concentration_mgm3=4.5,
            vlep_ratio=0.9,
            alert_level=FumesAlertLevel.ORANGE,
            exposure_minutes=30.5,
        )
        
        d = m.to_dict()
        
        assert d["fumes_concentration"] == 4.5
        assert d["fumes_vlep_ratio"] == 0.9
        assert d["fumes_alert_name"] == "ORANGE"
        assert d["fumes_exposure_minutes"] == 30.5
        assert d["fumes_requires_alert"] is True
    
    @pytest.mark.asyncio
    async def test_welding_active_simulation(self, simulator):
        """Test simulation soudage actif."""
        await simulator.connect()
        
        simulator.set_welding_active(True)
        # La concentration devrait être plus élevée
        # (testé via la boucle de simulation)
        
        simulator.set_welding_active(False)
        # La concentration devrait diminuer


class TestIntegration:
    """Tests d'intégration basiques."""
    
    @pytest.mark.asyncio
    async def test_all_simulators_connect(self):
        """Test que tous les simulateurs peuvent se connecter."""
        s7 = SiemensS7Simulator()
        fanuc = FanucSimulator()
        scanner = SICKScannerSimulator()
        fumes = FumesSensorSimulator()
        
        assert await s7.connect()
        assert await fanuc.connect()
        assert await scanner.connect()
        assert await fumes.connect()
        
        await s7.disconnect()
        await fanuc.disconnect()
        await scanner.disconnect()
        await fumes.disconnect()
    
    @pytest.mark.asyncio
    async def test_callbacks_registered(self):
        """Test enregistrement callbacks."""
        received = []
        
        simulator = FumesSensorSimulator()
        simulator.on_measurement(lambda m: received.append(m))
        
        await simulator.connect()
        await simulator.start_cyclic_read(100)
        
        # Attendre quelques mesures
        import asyncio
        await asyncio.sleep(0.3)
        
        await simulator.stop_cyclic_read()
        await simulator.disconnect()
        
        assert len(received) >= 2
