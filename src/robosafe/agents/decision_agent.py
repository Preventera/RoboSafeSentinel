"""
DecisionAgent - Agent de décision (Niveau 4).

Responsabilités:
- Évaluer les scores de risque
- Déterminer les actions appropriées
- Appliquer la matrice de décision
- Recommander des interventions

Inputs:
- Scores de risque de AnalysisAgent
- Règles de sécurité

Outputs:
- Recommandations d'intervention pour OrchestratorAgent
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Any, Dict, List, Optional, Set
import structlog

from robosafe.agents.base_agent import (
    BaseAgent,
    AgentConfig,
    AgentLevel,
    AgentMessage,
    MessagePriority,
)

logger = structlog.get_logger(__name__)


class ActionType(IntEnum):
    """Types d'actions possibles."""
    NONE = 0
    LOG = 1
    ALERT = 2
    SLOW_50 = 3
    SLOW_25 = 4
    STOP = 5
    ESTOP = 6


class ActionUrgency(IntEnum):
    """Urgence de l'action."""
    LOW = 0       # Peut attendre
    NORMAL = 1    # Dans les 5 secondes
    HIGH = 2      # Dans 1 seconde
    IMMEDIATE = 3 # Immédiat


@dataclass
class ActionRecommendation:
    """Recommandation d'action."""
    id: str
    action: ActionType
    urgency: ActionUrgency
    reason: str
    risk_category: str
    risk_score: float
    confidence: float
    timestamp: datetime = field(default_factory=datetime.now)
    
    # Métadonnées
    suppression_duration_s: float = 0  # Durée avant nouvelle alerte identique
    requires_ack: bool = False         # Nécessite acquittement opérateur
    auto_execute: bool = True          # Peut être exécutée automatiquement
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "action": self.action.name,
            "urgency": self.urgency.name,
            "reason": self.reason,
            "risk_category": self.risk_category,
            "risk_score": self.risk_score,
            "confidence": self.confidence,
            "auto_execute": self.auto_execute,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class DecisionConfig(AgentConfig):
    """Configuration de l'agent de décision."""
    name: str = "decision"
    level: AgentLevel = AgentLevel.RECOMMEND
    cycle_time_ms: int = 100  # 10 Hz
    
    # Seuils de décision (scores 0-100)
    threshold_alert: float = 25
    threshold_slow_50: float = 50
    threshold_slow_25: float = 65
    threshold_stop: float = 80
    threshold_estop: float = 95
    
    # Confiance minimale pour agir
    min_confidence: float = 0.7
    
    # Délai minimum entre actions identiques
    action_cooldown_s: float = 2.0
    
    # Mode de fonctionnement
    auto_execute_enabled: bool = True


class DecisionAgent(BaseAgent):
    """
    Agent de décision - Niveau 4 AgenticX5.
    
    Évalue les risques et recommande des actions.
    
    Matrice de décision:
    - Score 0-25: LOG uniquement
    - Score 25-50: ALERT opérateur
    - Score 50-65: SLOW 50%
    - Score 65-80: SLOW 25%
    - Score 80-95: STOP
    - Score 95+: E-STOP
    """
    
    # Message types
    MSG_RECOMMENDATION = "action_recommendation"
    MSG_DECISION_LOG = "decision_log"
    
    def __init__(self, config: Optional[DecisionConfig] = None):
        super().__init__(config or DecisionConfig())
        self.config: DecisionConfig = self.config
        
        # État actuel des risques
        self._current_risks: Dict[str, Dict[str, Any]] = {}
        self._global_risk: Dict[str, Any] = {}
        self._patterns: List[Dict[str, Any]] = []
        
        # Historique des actions pour éviter le spam
        self._action_history: Dict[str, datetime] = {}
        
        # Recommandations actives
        self._active_recommendations: List[ActionRecommendation] = []
        
        # Stats
        self._decisions_made = 0
        self._actions_recommended = 0
        
        # ID counter
        self._action_id = 0
    
    async def cycle(self) -> None:
        """Cycle principal: évalue et décide."""
        if not self._global_risk:
            return
        
        # Nettoyer anciennes recommandations
        self._cleanup_old_recommendations()
        
        # Évaluer le risque global
        global_score = self._global_risk.get("score", 0)
        
        # Déterminer l'action recommandée
        recommendation = self._evaluate_and_recommend()
        
        if recommendation:
            # Vérifier cooldown
            if self._check_cooldown(recommendation):
                self._active_recommendations.append(recommendation)
                self._actions_recommended += 1
                
                # Envoyer à l'orchestrateur
                self.send_to(
                    target="orchestrator",
                    msg_type=self.MSG_RECOMMENDATION,
                    payload=recommendation.to_dict(),
                    priority=self._get_message_priority(recommendation.urgency),
                )
                
                logger.info(
                    "action_recommended",
                    agent=self.id,
                    action=recommendation.action.name,
                    reason=recommendation.reason,
                    score=recommendation.risk_score,
                )
        
        self._decisions_made += 1
    
    async def handle_message(self, message: AgentMessage) -> None:
        """Traite les messages reçus."""
        if message.type == "risk_update":
            self._global_risk = message.payload.get("global_risk", {})
            self._current_risks = message.payload.get("category_risks", {})
            self._patterns = message.payload.get("patterns", [])
    
    def _evaluate_and_recommend(self) -> Optional[ActionRecommendation]:
        """Évalue les risques et génère une recommandation."""
        score = self._global_risk.get("score", 0)
        level = self._global_risk.get("level", "NONE")
        factors = self._global_risk.get("factors", [])
        confidence = self._global_risk.get("confidence", 0)
        
        # Vérifier la confiance
        if confidence < self.config.min_confidence:
            return None
        
        # Déterminer l'action basée sur le score
        action, urgency = self._determine_action(score)
        
        if action == ActionType.NONE:
            return None
        
        # Trouver le risque dominant
        dominant_risk = self._find_dominant_risk()
        
        # Créer la recommandation
        self._action_id += 1
        
        return ActionRecommendation(
            id=f"REC-{self._action_id:05d}",
            action=action,
            urgency=urgency,
            reason=self._format_reason(dominant_risk, factors),
            risk_category=dominant_risk,
            risk_score=score,
            confidence=confidence,
            auto_execute=self._can_auto_execute(action),
            requires_ack=action >= ActionType.STOP,
            suppression_duration_s=self.config.action_cooldown_s,
        )
    
    def _determine_action(self, score: float) -> tuple[ActionType, ActionUrgency]:
        """Détermine l'action basée sur le score."""
        if score >= self.config.threshold_estop:
            return ActionType.ESTOP, ActionUrgency.IMMEDIATE
        elif score >= self.config.threshold_stop:
            return ActionType.STOP, ActionUrgency.IMMEDIATE
        elif score >= self.config.threshold_slow_25:
            return ActionType.SLOW_25, ActionUrgency.HIGH
        elif score >= self.config.threshold_slow_50:
            return ActionType.SLOW_50, ActionUrgency.HIGH
        elif score >= self.config.threshold_alert:
            return ActionType.ALERT, ActionUrgency.NORMAL
        else:
            return ActionType.NONE, ActionUrgency.LOW
    
    def _find_dominant_risk(self) -> str:
        """Trouve la catégorie de risque dominante."""
        if not self._current_risks:
            return "unknown"
        
        max_score = 0
        dominant = "unknown"
        
        for category, risk in self._current_risks.items():
            score = risk.get("score", 0)
            if score > max_score:
                max_score = score
                dominant = category
        
        return dominant
    
    def _format_reason(self, dominant_risk: str, factors: List[str]) -> str:
        """Formate la raison de la recommandation."""
        reason_parts = [f"Risque {dominant_risk} élevé"]
        
        if factors:
            reason_parts.append(f"Facteurs: {', '.join(factors[:3])}")
        
        # Ajouter patterns si présents
        if self._patterns:
            pattern_types = [p.get("type", "") for p in self._patterns[:2]]
            if pattern_types:
                reason_parts.append(f"Patterns: {', '.join(pattern_types)}")
        
        return ". ".join(reason_parts)
    
    def _can_auto_execute(self, action: ActionType) -> bool:
        """Vérifie si l'action peut être auto-exécutée."""
        if not self.config.auto_execute_enabled:
            return False
        
        # Les actions critiques peuvent toujours être auto-exécutées
        # Les actions moins critiques respectent la config
        return action >= ActionType.SLOW_50
    
    def _check_cooldown(self, recommendation: ActionRecommendation) -> bool:
        """Vérifie si une action similaire n'a pas été recommandée récemment."""
        key = f"{recommendation.action.name}_{recommendation.risk_category}"
        
        if key in self._action_history:
            last_time = self._action_history[key]
            elapsed = (datetime.now() - last_time).total_seconds()
            
            if elapsed < self.config.action_cooldown_s:
                return False
        
        # Enregistrer cette action
        self._action_history[key] = datetime.now()
        return True
    
    def _get_message_priority(self, urgency: ActionUrgency) -> MessagePriority:
        """Convertit l'urgence en priorité de message."""
        mapping = {
            ActionUrgency.LOW: MessagePriority.LOW,
            ActionUrgency.NORMAL: MessagePriority.NORMAL,
            ActionUrgency.HIGH: MessagePriority.HIGH,
            ActionUrgency.IMMEDIATE: MessagePriority.CRITICAL,
        }
        return mapping.get(urgency, MessagePriority.NORMAL)
    
    def _cleanup_old_recommendations(self) -> None:
        """Nettoie les recommandations expirées."""
        cutoff = datetime.now() - timedelta(seconds=30)
        self._active_recommendations = [
            r for r in self._active_recommendations
            if r.timestamp > cutoff
        ]
    
    def get_active_recommendations(self) -> List[ActionRecommendation]:
        """Récupère les recommandations actives."""
        return self._active_recommendations.copy()
