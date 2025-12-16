"""
AnalysisAgent - Agent d'analyse (Niveau 3).

Responsabilités:
- Analyse des signaux normalisés
- Détection de patterns/anomalies
- Calcul de scores de risque
- Corrélation multi-capteurs

Inputs:
- Signaux normalisés de PerceptionAgent

Outputs:
- Scores de risque pour DecisionAgent
- Alertes de patterns détectés
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple
import structlog

from robosafe.agents.base_agent import (
    BaseAgent,
    AgentConfig,
    AgentLevel,
    AgentMessage,
    MessagePriority,
)

logger = structlog.get_logger(__name__)


class RiskLevel(IntEnum):
    """Niveaux de risque."""
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class TrendDirection(IntEnum):
    """Direction de tendance."""
    STABLE = 0
    INCREASING = 1
    DECREASING = -1


@dataclass
class RiskScore:
    """Score de risque calculé."""
    category: str          # distance, collision, exposure, equipment
    level: RiskLevel
    score: float          # 0-100
    confidence: float     # 0-1
    factors: List[str]    # Facteurs contributifs
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "level": self.level.name,
            "score": self.score,
            "confidence": self.confidence,
            "factors": self.factors,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class PatternAlert:
    """Alerte de pattern détecté."""
    pattern_type: str     # approach, oscillation, drift, spike
    severity: RiskLevel
    description: str
    signals_involved: List[str]
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class AnalysisConfig(AgentConfig):
    """Configuration de l'agent d'analyse."""
    name: str = "analysis"
    level: AgentLevel = AgentLevel.ANALYZE
    cycle_time_ms: int = 100  # 10 Hz
    
    # Seuils de risque distance
    distance_critical_mm: int = 500
    distance_high_mm: int = 800
    distance_medium_mm: int = 1200
    distance_low_mm: int = 2000
    
    # Seuils fumées (ratio VLEP)
    fumes_critical: float = 1.2
    fumes_high: float = 1.0
    fumes_medium: float = 0.8
    fumes_low: float = 0.5
    
    # Détection patterns
    pattern_window_seconds: float = 5.0
    approach_rate_threshold_mms: float = 500.0  # Vitesse d'approche critique
    oscillation_threshold: int = 5  # Changements de direction


class AnalysisAgent(BaseAgent):
    """
    Agent d'analyse - Niveau 3 AgenticX5.
    
    Analyse les signaux et calcule des scores de risque.
    
    Catégories de risque:
    - Distance: Risque de collision basé sur distance
    - Collision: Combinaison distance + vitesse robot
    - Exposure: Exposition aux fumées
    - Equipment: État équipements (EPI, capteurs)
    """
    
    # Message types
    MSG_RISK_UPDATE = "risk_update"
    MSG_PATTERN_ALERT = "pattern_alert"
    MSG_ANALYSIS_SUMMARY = "analysis_summary"
    
    def __init__(self, config: Optional[AnalysisConfig] = None):
        super().__init__(config or AnalysisConfig())
        self.config: AnalysisConfig = self.config
        
        # Stockage des signaux reçus
        self._current_signals: Dict[str, Dict[str, Any]] = {}
        self._signal_history: Dict[str, List[Tuple[datetime, float]]] = {}
        
        # Scores de risque actuels
        self._risk_scores: Dict[str, RiskScore] = {}
        
        # Patterns détectés
        self._active_patterns: List[PatternAlert] = []
        
        # Stats
        self._analyses_performed = 0
        self._patterns_detected = 0
    
    async def cycle(self) -> None:
        """Cycle principal: analyse les signaux."""
        if not self._current_signals:
            return
        
        # 1. Calculer les scores de risque
        self._calculate_distance_risk()
        self._calculate_collision_risk()
        self._calculate_exposure_risk()
        self._calculate_equipment_risk()
        
        # 2. Détecter les patterns
        self._detect_patterns()
        
        # 3. Calculer le risque global
        global_risk = self._calculate_global_risk()
        
        # 4. Envoyer à DecisionAgent
        self.send_to(
            target="decision",
            msg_type=self.MSG_RISK_UPDATE,
            payload={
                "global_risk": global_risk.to_dict(),
                "category_risks": {
                    k: v.to_dict() for k, v in self._risk_scores.items()
                },
                "patterns": [
                    {
                        "type": p.pattern_type,
                        "severity": p.severity.name,
                        "description": p.description,
                    }
                    for p in self._active_patterns
                ],
                "timestamp": datetime.now().isoformat(),
            },
            priority=MessagePriority.HIGH,
        )
        
        self._analyses_performed += 1
    
    async def handle_message(self, message: AgentMessage) -> None:
        """Traite les messages reçus."""
        if message.type == "signal_batch":
            # Mettre à jour les signaux
            signals = message.payload.get("signals", [])
            for sig in signals:
                sig_id = sig["id"]
                self._current_signals[sig_id] = sig
                self._update_history(sig_id, sig["value"])
        
        elif message.type == "quality_alert":
            # Intégrer les alertes qualité dans l'analyse
            signal_id = message.payload.get("signal_id")
            if signal_id:
                # Marquer le signal comme dégradé
                if signal_id in self._current_signals:
                    self._current_signals[signal_id]["quality"] = "degraded"
    
    def _update_history(self, signal_id: str, value: float) -> None:
        """Met à jour l'historique d'un signal."""
        if signal_id not in self._signal_history:
            self._signal_history[signal_id] = []
        
        history = self._signal_history[signal_id]
        history.append((datetime.now(), value))
        
        # Garder seulement la fenêtre d'analyse
        cutoff = datetime.now() - timedelta(seconds=self.config.pattern_window_seconds)
        self._signal_history[signal_id] = [
            (t, v) for t, v in history if t > cutoff
        ]
    
    def _calculate_distance_risk(self) -> None:
        """Calcule le risque basé sur la distance."""
        # Récupérer les distances
        scanner_dist = self._get_signal_value("scanner_min_distance", 10000)
        vision_dist = self._get_signal_value("vision_min_distance", 10000)
        
        # Prendre la distance minimale
        min_distance = min(scanner_dist, vision_dist)
        
        # Déterminer le niveau de risque
        if min_distance <= self.config.distance_critical_mm:
            level = RiskLevel.CRITICAL
            score = 100
        elif min_distance <= self.config.distance_high_mm:
            level = RiskLevel.HIGH
            score = 75 + 25 * (self.config.distance_high_mm - min_distance) / (
                self.config.distance_high_mm - self.config.distance_critical_mm
            )
        elif min_distance <= self.config.distance_medium_mm:
            level = RiskLevel.MEDIUM
            score = 50 + 25 * (self.config.distance_medium_mm - min_distance) / (
                self.config.distance_medium_mm - self.config.distance_high_mm
            )
        elif min_distance <= self.config.distance_low_mm:
            level = RiskLevel.LOW
            score = 25 + 25 * (self.config.distance_low_mm - min_distance) / (
                self.config.distance_low_mm - self.config.distance_medium_mm
            )
        else:
            level = RiskLevel.NONE
            score = 0
        
        factors = []
        if scanner_dist < 2000:
            factors.append(f"Scanner: {scanner_dist}mm")
        if vision_dist < 2000:
            factors.append(f"Vision: {vision_dist}mm")
        
        self._risk_scores["distance"] = RiskScore(
            category="distance",
            level=level,
            score=score,
            confidence=0.9 if scanner_dist < 10000 else 0.7,
            factors=factors,
        )
    
    def _calculate_collision_risk(self) -> None:
        """Calcule le risque de collision (distance + vitesse)."""
        distance = self._get_signal_value("scanner_min_distance", 10000)
        robot_speed = self._get_signal_value("fanuc_tcp_speed", 0)
        
        # Temps avant collision potentielle
        if robot_speed > 0 and distance < 5000:
            time_to_collision = distance / robot_speed  # secondes
        else:
            time_to_collision = float('inf')
        
        # Score basé sur le temps avant collision
        if time_to_collision < 0.5:
            level = RiskLevel.CRITICAL
            score = 100
        elif time_to_collision < 1.0:
            level = RiskLevel.HIGH
            score = 80
        elif time_to_collision < 2.0:
            level = RiskLevel.MEDIUM
            score = 50
        elif time_to_collision < 5.0:
            level = RiskLevel.LOW
            score = 25
        else:
            level = RiskLevel.NONE
            score = 0
        
        factors = [
            f"Distance: {distance}mm",
            f"Vitesse robot: {robot_speed}mm/s",
            f"TTC: {time_to_collision:.1f}s" if time_to_collision < 100 else "TTC: >100s",
        ]
        
        self._risk_scores["collision"] = RiskScore(
            category="collision",
            level=level,
            score=score,
            confidence=0.85,
            factors=factors,
        )
    
    def _calculate_exposure_risk(self) -> None:
        """Calcule le risque d'exposition aux fumées."""
        vlep_ratio = self._get_signal_value("fumes_vlep_ratio", 0)
        
        if vlep_ratio >= self.config.fumes_critical:
            level = RiskLevel.CRITICAL
            score = 100
        elif vlep_ratio >= self.config.fumes_high:
            level = RiskLevel.HIGH
            score = 75 + 25 * (vlep_ratio - self.config.fumes_high) / (
                self.config.fumes_critical - self.config.fumes_high
            )
        elif vlep_ratio >= self.config.fumes_medium:
            level = RiskLevel.MEDIUM
            score = 50 + 25 * (vlep_ratio - self.config.fumes_medium) / (
                self.config.fumes_high - self.config.fumes_medium
            )
        elif vlep_ratio >= self.config.fumes_low:
            level = RiskLevel.LOW
            score = 25 + 25 * (vlep_ratio - self.config.fumes_low) / (
                self.config.fumes_medium - self.config.fumes_low
            )
        else:
            level = RiskLevel.NONE
            score = 0
        
        self._risk_scores["exposure"] = RiskScore(
            category="exposure",
            level=level,
            score=score,
            confidence=0.95,
            factors=[f"VLEP ratio: {vlep_ratio:.2f}"],
        )
    
    def _calculate_equipment_risk(self) -> None:
        """Calcule le risque équipement (EPI, capteurs)."""
        factors = []
        issues = 0
        
        # Vérifier EPI
        ppe_ok = self._get_signal_value("vision_ppe_ok", 1)
        if not ppe_ok:
            issues += 2
            factors.append("EPI manquant détecté")
        
        # Vérifier qualité signaux critiques
        for sig_id in ["scanner_min_distance", "plc_heartbeat", "estop_status"]:
            sig = self._current_signals.get(sig_id, {})
            if sig.get("quality") in ["timeout", "degraded", "bad"]:
                issues += 1
                factors.append(f"Signal {sig_id} dégradé")
        
        # Calculer score
        if issues >= 3:
            level = RiskLevel.HIGH
            score = 75
        elif issues >= 2:
            level = RiskLevel.MEDIUM
            score = 50
        elif issues >= 1:
            level = RiskLevel.LOW
            score = 25
        else:
            level = RiskLevel.NONE
            score = 0
        
        self._risk_scores["equipment"] = RiskScore(
            category="equipment",
            level=level,
            score=score,
            confidence=0.9,
            factors=factors if factors else ["Tous équipements OK"],
        )
    
    def _calculate_global_risk(self) -> RiskScore:
        """Calcule le score de risque global."""
        if not self._risk_scores:
            return RiskScore(
                category="global",
                level=RiskLevel.NONE,
                score=0,
                confidence=0,
                factors=[],
            )
        
        # Pondération des catégories
        weights = {
            "collision": 0.35,
            "distance": 0.30,
            "exposure": 0.20,
            "equipment": 0.15,
        }
        
        # Score pondéré
        weighted_score = 0
        total_weight = 0
        factors = []
        max_level = RiskLevel.NONE
        
        for category, risk in self._risk_scores.items():
            weight = weights.get(category, 0.1)
            weighted_score += risk.score * weight
            total_weight += weight
            
            if risk.level > max_level:
                max_level = risk.level
            
            if risk.level >= RiskLevel.MEDIUM:
                factors.append(f"{category}: {risk.level.name}")
        
        global_score = weighted_score / total_weight if total_weight > 0 else 0
        
        return RiskScore(
            category="global",
            level=max_level,
            score=global_score,
            confidence=0.85,
            factors=factors,
        )
    
    def _detect_patterns(self) -> None:
        """Détecte les patterns dans l'historique des signaux."""
        self._active_patterns = []
        
        # Pattern: Approche rapide
        self._detect_approach_pattern()
        
        # Pattern: Oscillation (aller-retour)
        self._detect_oscillation_pattern()
        
        # Pattern: Dérive (drift)
        self._detect_drift_pattern()
    
    def _detect_approach_pattern(self) -> None:
        """Détecte une approche rapide vers le robot."""
        history = self._signal_history.get("scanner_min_distance", [])
        
        if len(history) < 5:
            return
        
        # Calculer la vitesse d'approche moyenne
        distances = [v for _, v in history]
        if len(distances) >= 2:
            time_span = (history[-1][0] - history[0][0]).total_seconds()
            if time_span > 0:
                approach_rate = (distances[0] - distances[-1]) / time_span
                
                if approach_rate > self.config.approach_rate_threshold_mms:
                    self._active_patterns.append(PatternAlert(
                        pattern_type="rapid_approach",
                        severity=RiskLevel.HIGH,
                        description=f"Approche rapide détectée: {approach_rate:.0f} mm/s",
                        signals_involved=["scanner_min_distance"],
                    ))
                    self._patterns_detected += 1
    
    def _detect_oscillation_pattern(self) -> None:
        """Détecte des oscillations (entrées/sorties répétées)."""
        history = self._signal_history.get("scanner_zone_status", [])
        
        if len(history) < 5:
            return
        
        # Compter les changements de direction
        values = [v for _, v in history]
        direction_changes = 0
        
        for i in range(2, len(values)):
            if (values[i] - values[i-1]) * (values[i-1] - values[i-2]) < 0:
                direction_changes += 1
        
        if direction_changes >= self.config.oscillation_threshold:
            self._active_patterns.append(PatternAlert(
                pattern_type="oscillation",
                severity=RiskLevel.MEDIUM,
                description=f"Oscillation détectée: {direction_changes} changements",
                signals_involved=["scanner_zone_status"],
            ))
            self._patterns_detected += 1
    
    def _detect_drift_pattern(self) -> None:
        """Détecte une dérive progressive d'un signal."""
        history = self._signal_history.get("fumes_vlep_ratio", [])
        
        if len(history) < 10:
            return
        
        values = [v for _, v in history]
        
        # Calculer la tendance
        first_half = sum(values[:len(values)//2]) / (len(values)//2)
        second_half = sum(values[len(values)//2:]) / (len(values) - len(values)//2)
        
        drift = second_half - first_half
        
        if drift > 0.2:  # Dérive significative vers le haut
            self._active_patterns.append(PatternAlert(
                pattern_type="drift_up",
                severity=RiskLevel.MEDIUM,
                description=f"Dérive croissante des fumées: +{drift:.2f}",
                signals_involved=["fumes_vlep_ratio"],
            ))
            self._patterns_detected += 1
    
    def _get_signal_value(self, signal_id: str, default: float = 0) -> float:
        """Récupère la valeur d'un signal."""
        sig = self._current_signals.get(signal_id, {})
        return sig.get("value", default)
    
    def get_risk_scores(self) -> Dict[str, RiskScore]:
        """Récupère tous les scores de risque."""
        return self._risk_scores.copy()
    
    def get_global_risk(self) -> RiskScore:
        """Récupère le risque global."""
        return self._calculate_global_risk()
