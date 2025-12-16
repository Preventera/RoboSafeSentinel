"""
Tests unitaires pour le driver Vision IA.
"""

import pytest
from datetime import datetime

from robosafe.sensors.vision_ai import (
    VisionSimulator,
    VisionConfig,
    VisionResult,
    DetectedPerson,
    PPEType,
    PostureRisk,
)


class TestPPEType:
    """Tests pour les flags PPE."""
    
    def test_ppe_none(self):
        """Test PPE vide."""
        assert PPEType.NONE.value == 0
    
    def test_ppe_combination(self):
        """Test combinaison PPE."""
        required = PPEType.SAFETY_GLASSES | PPEType.GLOVES
        
        assert required & PPEType.SAFETY_GLASSES
        assert required & PPEType.GLOVES
        assert not (required & PPEType.HELMET)
    
    def test_ppe_missing_calculation(self):
        """Test calcul EPI manquants."""
        required = PPEType.SAFETY_GLASSES | PPEType.GLOVES | PPEType.HELMET
        detected = PPEType.SAFETY_GLASSES | PPEType.GLOVES
        
        missing = required & ~detected
        
        assert missing == PPEType.HELMET
        assert not (missing & PPEType.SAFETY_GLASSES)
        assert not (missing & PPEType.GLOVES)


class TestPostureRisk:
    """Tests pour les niveaux de risque posture."""
    
    def test_risk_levels(self):
        """Test ordre des niveaux."""
        assert PostureRisk.LOW.value < PostureRisk.MEDIUM.value
        assert PostureRisk.MEDIUM.value < PostureRisk.HIGH.value
        assert PostureRisk.HIGH.value < PostureRisk.VERY_HIGH.value
    
    def test_risk_comparison(self):
        """Test comparaison."""
        risks = [PostureRisk.HIGH, PostureRisk.LOW, PostureRisk.MEDIUM]
        assert max(risks) == PostureRisk.HIGH


class TestDetectedPerson:
    """Tests pour DetectedPerson."""
    
    @pytest.fixture
    def person(self):
        return DetectedPerson(
            id=1,
            bbox=(100, 100, 200, 400),  # 100x300 pixels
            confidence=0.85,
            distance_mm=2000.0,
            ppe_detected=PPEType.SAFETY_GLASSES,
            ppe_missing=PPEType.GLOVES,
            posture_risk=PostureRisk.LOW,
            in_danger_zone=False,
        )
    
    def test_center(self, person):
        """Test calcul centre."""
        cx, cy = person.center
        assert cx == 150  # (100 + 200) / 2
        assert cy == 250  # (100 + 400) / 2
    
    def test_height_px(self, person):
        """Test hauteur pixels."""
        assert person.height_px == 300  # 400 - 100
    
    def test_in_danger_zone(self, person):
        """Test zone danger."""
        assert person.in_danger_zone is False
        
        danger_person = DetectedPerson(
            id=2,
            bbox=(0, 0, 100, 100),
            confidence=0.9,
            distance_mm=400.0,
            ppe_detected=PPEType.NONE,
            ppe_missing=PPEType.SAFETY_GLASSES,
            posture_risk=PostureRisk.HIGH,
            in_danger_zone=True,
        )
        assert danger_person.in_danger_zone is True


class TestVisionResult:
    """Tests pour VisionResult."""
    
    def test_empty_result(self):
        """Test résultat vide."""
        result = VisionResult()
        
        assert result.persons_detected == 0
        assert len(result.persons) == 0
        assert result.min_distance_mm == float('inf')
        assert result.all_ppe_ok is True
        assert result.intrusion_detected is False
    
    def test_result_with_persons(self):
        """Test résultat avec personnes."""
        persons = [
            DetectedPerson(
                id=1, bbox=(0, 0, 100, 300),
                confidence=0.9, distance_mm=1500.0,
                ppe_detected=PPEType.SAFETY_GLASSES,
                ppe_missing=PPEType.GLOVES,
                posture_risk=PostureRisk.LOW,
                in_danger_zone=False,
            ),
            DetectedPerson(
                id=2, bbox=(200, 0, 300, 400),
                confidence=0.85, distance_mm=2500.0,
                ppe_detected=PPEType.SAFETY_GLASSES | PPEType.GLOVES,
                ppe_missing=PPEType.NONE,
                posture_risk=PostureRisk.MEDIUM,
                in_danger_zone=False,
            ),
        ]
        
        result = VisionResult(
            persons_detected=2,
            persons=persons,
            min_distance_mm=1500.0,
            closest_person_id=1,
            all_ppe_ok=False,
            missing_ppe_types=PPEType.GLOVES,
            ppe_alert=True,
        )
        
        assert result.persons_detected == 2
        assert result.min_distance_mm == 1500.0
        assert result.ppe_alert is True
    
    def test_to_dict(self):
        """Test conversion dict."""
        result = VisionResult(
            persons_detected=1,
            min_distance_mm=1200.0,
            confidence_avg=0.88,
            all_ppe_ok=True,
            intrusion_detected=False,
        )
        
        d = result.to_dict()
        
        assert d["vision_presence"] is True
        assert d["vision_person_count"] == 1
        assert d["vision_min_distance"] == 1200.0
        assert d["vision_confidence"] == 0.88
        assert d["vision_ppe_ok"] is True
        assert d["vision_intrusion"] is False
    
    def test_to_dict_no_detection(self):
        """Test conversion dict sans détection."""
        result = VisionResult()
        d = result.to_dict()
        
        assert d["vision_presence"] is False
        assert d["vision_min_distance"] == 10000  # Valeur par défaut


class TestVisionSimulator:
    """Tests pour le simulateur Vision."""
    
    @pytest.fixture
    def simulator(self):
        return VisionSimulator()
    
    @pytest.mark.asyncio
    async def test_connect(self, simulator):
        """Test connexion."""
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
    async def test_callbacks(self, simulator):
        """Test callbacks."""
        results_received = []
        
        simulator.on_result(lambda r: results_received.append(r))
        
        await simulator.connect()
        await simulator.start_processing(50)
        
        import asyncio
        await asyncio.sleep(0.2)
        
        await simulator.stop_processing()
        await simulator.disconnect()
        
        assert len(results_received) >= 2
        assert all(isinstance(r, VisionResult) for r in results_received)
    
    @pytest.mark.asyncio
    async def test_intrusion_callback(self, simulator):
        """Test callback intrusion."""
        intrusions = []
        
        simulator.on_intrusion(lambda p: intrusions.append(p))
        
        await simulator.connect()
        await simulator.start_processing(20)
        
        import asyncio
        await asyncio.sleep(1.0)  # Attendre assez pour avoir une intrusion (5% chance)
        
        await simulator.stop_processing()
        await simulator.disconnect()
        
        # Il peut y avoir ou non des intrusions (aléatoire)
        for intrusion in intrusions:
            assert isinstance(intrusion, DetectedPerson)
            assert intrusion.in_danger_zone is True


class TestVisionConfig:
    """Tests pour VisionConfig."""
    
    def test_default_config(self):
        """Test configuration par défaut."""
        config = VisionConfig()
        
        assert config.camera_source == "0"
        assert config.width == 1920
        assert config.height == 1080
        assert config.fps == 30
        assert config.confidence_threshold == 0.5
    
    def test_custom_config(self):
        """Test configuration personnalisée."""
        config = VisionConfig(
            camera_source="rtsp://192.168.1.100/stream",
            camera_type="rtsp",
            width=1280,
            height=720,
            confidence_threshold=0.7,
        )
        
        assert config.camera_source == "rtsp://192.168.1.100/stream"
        assert config.camera_type == "rtsp"
        assert config.width == 1280


class TestDistanceEstimation:
    """Tests pour l'estimation de distance."""
    
    def test_distance_formula(self):
        """Test formule de distance."""
        # distance = (hauteur_réelle × focale) / hauteur_pixels
        known_height_mm = 1700.0
        focal_length_px = 800.0
        
        # Personne proche (grande dans l'image)
        height_px_close = 400
        distance_close = (known_height_mm * focal_length_px) / height_px_close
        assert distance_close == 3400.0  # mm
        
        # Personne loin (petite dans l'image)
        height_px_far = 100
        distance_far = (known_height_mm * focal_length_px) / height_px_far
        assert distance_far == 13600.0  # mm
    
    def test_calibration_formula(self):
        """Test formule de calibration."""
        # focale = (hauteur_px × distance) / hauteur_réelle
        known_distance_mm = 2000.0
        measured_height_px = 300
        known_height_mm = 1700.0
        
        focal_length = (measured_height_px * known_distance_mm) / known_height_mm
        assert round(focal_length, 1) == 352.9


class TestIntegration:
    """Tests d'intégration basiques."""
    
    @pytest.mark.asyncio
    async def test_full_simulation_cycle(self):
        """Test cycle complet de simulation."""
        config = VisionConfig(
            confidence_threshold=0.6,
            known_height_mm=1700.0,
        )
        
        simulator = VisionSimulator(config)
        
        results = []
        simulator.on_result(lambda r: results.append(r))
        
        assert await simulator.connect()
        await simulator.start_processing(30)
        
        import asyncio
        await asyncio.sleep(0.5)
        
        await simulator.stop_processing()
        await simulator.disconnect()
        
        assert len(results) >= 10
        
        # Vérifier que certains résultats ont des détections
        detections = [r for r in results if r.persons_detected > 0]
        # Il devrait y avoir des détections (~40% du temps)
        # Mais c'est aléatoire donc on ne peut pas garantir
        
        # Vérifier structure des résultats
        for result in results:
            assert isinstance(result.timestamp, datetime)
            assert result.processing_time_ms >= 0
            assert 0 <= result.persons_detected <= 10
