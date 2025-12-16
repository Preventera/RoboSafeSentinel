"""
Driver Vision IA pour RoboSafe Sentinel.

Détection en temps réel via caméra industrielle:
- Présence humaine (YOLO)
- Équipements de Protection Individuelle (EPI)
- Estimation de distance
- Analyse de posture (RULA simplifié)

Requires:
    pip install opencv-python ultralytics numpy

Compatibilité:
    - Caméras GigE Vision (Basler, IDS, FLIR)
    - Caméras USB
    - Flux RTSP
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum, IntFlag
from typing import Any, Callable, Dict, List, Optional, Tuple
import structlog

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    cv2 = None
    np = None

try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False
    YOLO = None

logger = structlog.get_logger(__name__)


class PPEType(IntFlag):
    """Types d'EPI détectables."""
    NONE = 0x00
    SAFETY_GLASSES = 0x01   # Lunettes de sécurité
    WELDING_MASK = 0x02     # Masque soudure
    GLOVES = 0x04           # Gants
    HELMET = 0x08           # Casque
    HIGH_VIS_VEST = 0x10    # Gilet haute visibilité
    RESPIRATOR = 0x20       # Masque respiratoire


class PostureRisk(IntEnum):
    """Niveau de risque posture (RULA simplifié)."""
    LOW = 1          # Score 1-2: OK
    MEDIUM = 2       # Score 3-4: Investiguer
    HIGH = 3         # Score 5-6: Bientôt action
    VERY_HIGH = 4    # Score 7+: Action immédiate


@dataclass
class VisionConfig:
    """Configuration du système de vision."""
    # Source vidéo
    camera_source: str = "0"  # Index, IP, ou chemin RTSP
    camera_type: str = "usb"  # usb, gige, rtsp
    
    # Résolution
    width: int = 1920
    height: int = 1080
    fps: int = 30
    
    # Modèles IA
    yolo_model: str = "yolov8n.pt"  # nano pour vitesse
    ppe_model: Optional[str] = None  # Modèle custom EPI
    
    # Seuils détection
    confidence_threshold: float = 0.5
    iou_threshold: float = 0.45
    
    # Calibration distance (à configurer selon installation)
    focal_length_px: float = 800.0    # Focale en pixels
    known_height_mm: float = 1700.0   # Hauteur humaine moyenne
    
    # Zones d'intérêt (ROI)
    roi_enabled: bool = False
    roi_points: List[Tuple[int, int]] = field(default_factory=list)
    
    # Timing
    process_interval_ms: int = 33  # ~30 FPS


@dataclass
class DetectedPerson:
    """Personne détectée."""
    id: int
    bbox: Tuple[int, int, int, int]  # x1, y1, x2, y2
    confidence: float
    distance_mm: float
    ppe_detected: PPEType
    ppe_missing: PPEType
    posture_risk: PostureRisk
    in_danger_zone: bool
    
    @property
    def center(self) -> Tuple[int, int]:
        """Centre du bounding box."""
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) // 2, (y1 + y2) // 2)
    
    @property
    def height_px(self) -> int:
        """Hauteur en pixels."""
        return self.bbox[3] - self.bbox[1]


@dataclass
class VisionResult:
    """Résultat d'analyse vision."""
    timestamp: datetime = field(default_factory=datetime.now)
    
    # Détections
    persons_detected: int = 0
    persons: List[DetectedPerson] = field(default_factory=list)
    
    # Distance minimale
    min_distance_mm: float = float('inf')
    closest_person_id: Optional[int] = None
    
    # EPI
    all_ppe_ok: bool = True
    missing_ppe_types: PPEType = PPEType.NONE
    
    # Posture
    max_posture_risk: PostureRisk = PostureRisk.LOW
    
    # Qualité
    frame_processed: bool = True
    processing_time_ms: float = 0.0
    confidence_avg: float = 0.0
    
    # Alertes
    intrusion_detected: bool = False
    ppe_alert: bool = False
    posture_alert: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit pour SignalManager."""
        return {
            "vision_presence": self.persons_detected > 0,
            "vision_person_count": self.persons_detected,
            "vision_min_distance": self.min_distance_mm if self.min_distance_mm != float('inf') else 10000,
            "vision_confidence": self.confidence_avg,
            "vision_ppe_ok": self.all_ppe_ok,
            "vision_ppe_missing": self.missing_ppe_types.value,
            "vision_posture_risk": self.max_posture_risk.value,
            "vision_intrusion": self.intrusion_detected,
            "vision_ppe_alert": self.ppe_alert,
            "vision_posture_alert": self.posture_alert,
            "vision_processing_ms": self.processing_time_ms,
        }


class VisionAIDriver:
    """
    Driver de vision IA pour détection sécurité.
    
    Fonctionnalités:
    - Détection présence humaine (YOLO)
    - Estimation distance par taille apparente
    - Détection EPI manquants
    - Analyse posture basique
    
    Pipeline:
    1. Capture frame
    2. Détection personnes (YOLO)
    3. Pour chaque personne:
       - Estimer distance
       - Vérifier EPI
       - Analyser posture
    4. Générer alertes
    """
    
    # Classes COCO pour personnes
    PERSON_CLASS_ID = 0
    
    def __init__(self, config: Optional[VisionConfig] = None):
        """
        Initialise le driver vision.
        
        Args:
            config: Configuration vision
        """
        if not CV2_AVAILABLE:
            raise ImportError("opencv-python not installed. Run: pip install opencv-python")
        
        self.config = config or VisionConfig()
        self._cap: Optional[cv2.VideoCapture] = None
        self._yolo_model: Optional[Any] = None
        self._ppe_model: Optional[Any] = None
        self._connected = False
        self._running = False
        self._process_task: Optional[asyncio.Task] = None
        
        # Tracking
        self._person_id_counter = 0
        self._tracked_persons: Dict[int, DetectedPerson] = {}
        
        # Callbacks
        self._on_result: List[Callable[[VisionResult], None]] = []
        self._on_intrusion: List[Callable[[DetectedPerson], None]] = []
        self._on_ppe_alert: List[Callable[[DetectedPerson, PPEType], None]] = []
        
        # État
        self._current_result = VisionResult()
        self._frame_count = 0
        
        logger.info(
            "vision_driver_initialized",
            source=self.config.camera_source,
            model=self.config.yolo_model,
        )
    
    @property
    def is_connected(self) -> bool:
        return self._connected and self._cap is not None
    
    @property
    def current_result(self) -> VisionResult:
        return self._current_result
    
    async def connect(self) -> bool:
        """
        Connecte à la caméra et charge les modèles.
        
        Returns:
            True si connecté
        """
        try:
            loop = asyncio.get_event_loop()
            
            # Ouvrir la caméra
            def _open_camera():
                source = self.config.camera_source
                
                # Déterminer le type de source
                if source.isdigit():
                    cap = cv2.VideoCapture(int(source))
                elif source.startswith("rtsp://"):
                    cap = cv2.VideoCapture(source)
                else:
                    cap = cv2.VideoCapture(source)
                
                if cap.isOpened():
                    # Configurer résolution
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
                    cap.set(cv2.CAP_PROP_FPS, self.config.fps)
                    return cap
                return None
            
            self._cap = await loop.run_in_executor(None, _open_camera)
            
            if self._cap is None:
                logger.error("vision_camera_open_failed", source=self.config.camera_source)
                return False
            
            # Charger modèle YOLO
            if YOLO_AVAILABLE:
                def _load_yolo():
                    return YOLO(self.config.yolo_model)
                
                self._yolo_model = await loop.run_in_executor(None, _load_yolo)
                logger.info("yolo_model_loaded", model=self.config.yolo_model)
            else:
                logger.warning("yolo_not_available", using="fallback_detection")
            
            self._connected = True
            logger.info(
                "vision_connected",
                width=int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                height=int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                fps=int(self._cap.get(cv2.CAP_PROP_FPS)),
            )
            
            return True
            
        except Exception as e:
            logger.error("vision_connect_error", error=str(e))
            self._connected = False
            return False
    
    async def disconnect(self) -> None:
        """Ferme la connexion caméra."""
        if self._cap:
            self._cap.release()
            self._cap = None
        
        self._connected = False
        logger.info("vision_disconnected")
    
    async def process_frame(self) -> Optional[VisionResult]:
        """
        Capture et analyse une frame.
        
        Returns:
            VisionResult ou None si erreur
        """
        if not self.is_connected:
            return None
        
        start_time = time.time()
        
        try:
            loop = asyncio.get_event_loop()
            
            # Capture frame
            def _capture():
                ret, frame = self._cap.read()
                return frame if ret else None
            
            frame = await loop.run_in_executor(None, _capture)
            
            if frame is None:
                return None
            
            self._frame_count += 1
            
            # Détection YOLO
            persons = await self._detect_persons(frame)
            
            # Créer résultat
            result = VisionResult(
                timestamp=datetime.now(),
                persons_detected=len(persons),
                persons=persons,
            )
            
            # Calculer métriques
            if persons:
                distances = [p.distance_mm for p in persons]
                result.min_distance_mm = min(distances)
                result.closest_person_id = min(persons, key=lambda p: p.distance_mm).id
                result.confidence_avg = sum(p.confidence for p in persons) / len(persons)
                
                # Vérifier EPI
                missing_ppe = PPEType.NONE
                for person in persons:
                    missing_ppe |= person.ppe_missing
                
                result.missing_ppe_types = missing_ppe
                result.all_ppe_ok = missing_ppe == PPEType.NONE
                result.ppe_alert = not result.all_ppe_ok
                
                # Posture max
                result.max_posture_risk = max(p.posture_risk for p in persons)
                result.posture_alert = result.max_posture_risk >= PostureRisk.HIGH
                
                # Intrusion (distance critique)
                result.intrusion_detected = result.min_distance_mm < 800
            
            result.processing_time_ms = (time.time() - start_time) * 1000
            
            self._current_result = result
            
            # Notifier
            await self._notify_callbacks(result)
            
            return result
            
        except Exception as e:
            logger.warning("vision_process_error", error=str(e))
            return None
    
    async def _detect_persons(self, frame: np.ndarray) -> List[DetectedPerson]:
        """
        Détecte les personnes dans la frame.
        
        Args:
            frame: Image BGR
            
        Returns:
            Liste des personnes détectées
        """
        persons = []
        
        if self._yolo_model is None:
            return persons
        
        try:
            loop = asyncio.get_event_loop()
            
            def _run_yolo():
                results = self._yolo_model(
                    frame,
                    conf=self.config.confidence_threshold,
                    iou=self.config.iou_threshold,
                    classes=[self.PERSON_CLASS_ID],  # Personnes uniquement
                    verbose=False,
                )
                return results
            
            results = await loop.run_in_executor(None, _run_yolo)
            
            if results and len(results) > 0:
                boxes = results[0].boxes
                
                for i, box in enumerate(boxes):
                    # Bounding box
                    x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                    confidence = float(box.conf[0])
                    
                    # Estimer distance
                    height_px = y2 - y1
                    distance_mm = self._estimate_distance(height_px)
                    
                    # Vérifier EPI (simplifié - à améliorer avec modèle custom)
                    ppe_detected, ppe_missing = await self._check_ppe(
                        frame[y1:y2, x1:x2]
                    )
                    
                    # Analyser posture (simplifié)
                    posture_risk = self._analyze_posture(box)
                    
                    # Vérifier zone danger
                    in_danger = distance_mm < 500
                    
                    self._person_id_counter += 1
                    
                    person = DetectedPerson(
                        id=self._person_id_counter,
                        bbox=(x1, y1, x2, y2),
                        confidence=confidence,
                        distance_mm=distance_mm,
                        ppe_detected=ppe_detected,
                        ppe_missing=ppe_missing,
                        posture_risk=posture_risk,
                        in_danger_zone=in_danger,
                    )
                    
                    persons.append(person)
            
        except Exception as e:
            logger.warning("yolo_detection_error", error=str(e))
        
        return persons
    
    def _estimate_distance(self, height_px: int) -> float:
        """
        Estime la distance basée sur la hauteur apparente.
        
        Formule: distance = (hauteur_réelle × focale) / hauteur_pixels
        
        Args:
            height_px: Hauteur en pixels
            
        Returns:
            Distance estimée en mm
        """
        if height_px <= 0:
            return float('inf')
        
        distance = (self.config.known_height_mm * self.config.focal_length_px) / height_px
        return max(0, distance)
    
    async def _check_ppe(self, person_roi: np.ndarray) -> Tuple[PPEType, PPEType]:
        """
        Vérifie les EPI sur une personne.
        
        Args:
            person_roi: Image ROI de la personne
            
        Returns:
            (EPI détectés, EPI manquants)
        """
        # Version simplifiée - détection basique par couleur/forme
        # En production: utiliser un modèle entraîné spécifiquement
        
        detected = PPEType.NONE
        required = PPEType.SAFETY_GLASSES | PPEType.GLOVES  # EPI requis soudage
        
        if person_roi.size == 0:
            return detected, required
        
        try:
            # Analyse basique couleur pour gilet haute visibilité
            hsv = cv2.cvtColor(person_roi, cv2.COLOR_BGR2HSV)
            
            # Détection jaune/orange (gilet)
            lower_yellow = np.array([20, 100, 100])
            upper_yellow = np.array([40, 255, 255])
            mask_yellow = cv2.inRange(hsv, lower_yellow, upper_yellow)
            
            if np.sum(mask_yellow) > 0.05 * mask_yellow.size * 255:
                detected |= PPEType.HIGH_VIS_VEST
            
            # Pour les autres EPI, un modèle dédié serait nécessaire
            # Ici on simule une détection partielle
            
        except Exception:
            pass
        
        missing = required & ~detected
        return detected, missing
    
    def _analyze_posture(self, box) -> PostureRisk:
        """
        Analyse basique de la posture.
        
        Args:
            box: Bounding box YOLO
            
        Returns:
            Niveau de risque posture
        """
        # Version simplifiée basée sur le ratio largeur/hauteur
        # En production: utiliser MediaPipe ou modèle pose estimation
        
        try:
            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
            width = x2 - x1
            height = y2 - y1
            
            if height == 0:
                return PostureRisk.LOW
            
            ratio = width / height
            
            # Ratio normal debout: ~0.3-0.4
            # Ratio penché/accroupi: >0.5
            if ratio > 0.7:
                return PostureRisk.HIGH
            elif ratio > 0.5:
                return PostureRisk.MEDIUM
            else:
                return PostureRisk.LOW
                
        except Exception:
            return PostureRisk.LOW
    
    async def _notify_callbacks(self, result: VisionResult) -> None:
        """Notifie les callbacks."""
        # Callback résultat
        for callback in self._on_result:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(result)
                else:
                    callback(result)
            except Exception as e:
                logger.error("vision_callback_error", error=str(e))
        
        # Callbacks intrusion
        if result.intrusion_detected:
            for person in result.persons:
                if person.in_danger_zone:
                    for callback in self._on_intrusion:
                        try:
                            callback(person)
                        except Exception:
                            pass
        
        # Callbacks EPI
        if result.ppe_alert:
            for person in result.persons:
                if person.ppe_missing != PPEType.NONE:
                    for callback in self._on_ppe_alert:
                        try:
                            callback(person, person.ppe_missing)
                        except Exception:
                            pass
    
    async def start_processing(self, interval_ms: float = 33) -> None:
        """Démarre le traitement continu."""
        if self._running:
            return
        
        self._running = True
        self._process_task = asyncio.create_task(
            self._processing_loop(interval_ms / 1000.0)
        )
        logger.info("vision_processing_started", interval_ms=interval_ms)
    
    async def stop_processing(self) -> None:
        """Arrête le traitement."""
        self._running = False
        if self._process_task:
            self._process_task.cancel()
            try:
                await self._process_task
            except asyncio.CancelledError:
                pass
        logger.info("vision_processing_stopped")
    
    async def _processing_loop(self, interval: float) -> None:
        """Boucle de traitement."""
        while self._running:
            if self.is_connected:
                await self.process_frame()
            
            await asyncio.sleep(interval)
    
    def on_result(self, callback: Callable[[VisionResult], None]) -> None:
        """Ajoute un callback pour les résultats."""
        self._on_result.append(callback)
    
    def on_intrusion(self, callback: Callable[[DetectedPerson], None]) -> None:
        """Ajoute un callback pour les intrusions."""
        self._on_intrusion.append(callback)
    
    def on_ppe_alert(self, callback: Callable[[DetectedPerson, PPEType], None]) -> None:
        """Ajoute un callback pour les alertes EPI."""
        self._on_ppe_alert.append(callback)
    
    def calibrate_distance(self, known_distance_mm: float, measured_height_px: int) -> None:
        """
        Calibre l'estimation de distance.
        
        Args:
            known_distance_mm: Distance réelle mesurée
            measured_height_px: Hauteur mesurée en pixels
        """
        # focale = (hauteur_px × distance) / hauteur_réelle
        self.config.focal_length_px = (
            measured_height_px * known_distance_mm
        ) / self.config.known_height_mm
        
        logger.info(
            "vision_distance_calibrated",
            focal_length=self.config.focal_length_px,
        )


class VisionSimulator:
    """
    Simulateur Vision IA pour tests.
    """
    
    def __init__(self, config: Optional[VisionConfig] = None):
        self.config = config or VisionConfig()
        self._running = False
        self._result = VisionResult()
        self._callbacks_result: List[Callable] = []
        self._callbacks_intrusion: List[Callable] = []
        self._callbacks_ppe: List[Callable] = []
        self._person_id = 0
    
    @property
    def is_connected(self) -> bool:
        return self._running
    
    @property
    def current_result(self) -> VisionResult:
        return self._result
    
    async def connect(self) -> bool:
        self._running = True
        logger.info("vision_simulator_connected")
        return True
    
    async def disconnect(self) -> None:
        self._running = False
        logger.info("vision_simulator_disconnected")
    
    async def start_processing(self, interval_ms: float = 33) -> None:
        asyncio.create_task(self._simulation_loop(interval_ms / 1000.0))
    
    async def stop_processing(self) -> None:
        self._running = False
    
    async def _simulation_loop(self, interval: float) -> None:
        import random
        
        while self._running:
            # Simuler différents scénarios
            scenario = random.random()
            
            persons = []
            
            if scenario < 0.60:  # 60% - Pas de personne
                pass
            elif scenario < 0.85:  # 25% - 1 personne loin
                self._person_id += 1
                persons.append(DetectedPerson(
                    id=self._person_id,
                    bbox=(100, 100, 200, 400),
                    confidence=random.uniform(0.7, 0.95),
                    distance_mm=random.uniform(2000, 5000),
                    ppe_detected=PPEType.SAFETY_GLASSES | PPEType.GLOVES,
                    ppe_missing=PPEType.NONE,
                    posture_risk=PostureRisk.LOW,
                    in_danger_zone=False,
                ))
            elif scenario < 0.95:  # 10% - 1 personne proche
                self._person_id += 1
                distance = random.uniform(500, 1500)
                persons.append(DetectedPerson(
                    id=self._person_id,
                    bbox=(300, 50, 500, 500),
                    confidence=random.uniform(0.8, 0.98),
                    distance_mm=distance,
                    ppe_detected=PPEType.SAFETY_GLASSES,
                    ppe_missing=PPEType.GLOVES,
                    posture_risk=PostureRisk.MEDIUM,
                    in_danger_zone=distance < 800,
                ))
            else:  # 5% - Intrusion zone danger
                self._person_id += 1
                persons.append(DetectedPerson(
                    id=self._person_id,
                    bbox=(400, 0, 700, 600),
                    confidence=random.uniform(0.85, 0.99),
                    distance_mm=random.uniform(200, 700),
                    ppe_detected=PPEType.NONE,
                    ppe_missing=PPEType.SAFETY_GLASSES | PPEType.GLOVES,
                    posture_risk=PostureRisk.HIGH,
                    in_danger_zone=True,
                ))
            
            # Créer résultat
            self._result = VisionResult(
                timestamp=datetime.now(),
                persons_detected=len(persons),
                persons=persons,
                processing_time_ms=random.uniform(15, 35),
            )
            
            if persons:
                self._result.min_distance_mm = min(p.distance_mm for p in persons)
                self._result.closest_person_id = min(persons, key=lambda p: p.distance_mm).id
                self._result.confidence_avg = sum(p.confidence for p in persons) / len(persons)
                
                missing = PPEType.NONE
                for p in persons:
                    missing |= p.ppe_missing
                self._result.missing_ppe_types = missing
                self._result.all_ppe_ok = missing == PPEType.NONE
                self._result.ppe_alert = not self._result.all_ppe_ok
                self._result.max_posture_risk = max(p.posture_risk for p in persons)
                self._result.posture_alert = self._result.max_posture_risk >= PostureRisk.HIGH
                self._result.intrusion_detected = any(p.in_danger_zone for p in persons)
            
            # Notifier
            for callback in self._callbacks_result:
                try:
                    callback(self._result)
                except Exception:
                    pass
            
            if self._result.intrusion_detected:
                for p in persons:
                    if p.in_danger_zone:
                        for callback in self._callbacks_intrusion:
                            try:
                                callback(p)
                            except Exception:
                                pass
            
            await asyncio.sleep(interval)
    
    def on_result(self, callback: Callable) -> None:
        self._callbacks_result.append(callback)
    
    def on_intrusion(self, callback: Callable) -> None:
        self._callbacks_intrusion.append(callback)
    
    def on_ppe_alert(self, callback: Callable) -> None:
        self._callbacks_ppe.append(callback)
