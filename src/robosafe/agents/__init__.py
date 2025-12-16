"""
Agents AgenticX5 pour RoboSafe Sentinel.

Architecture 5 niveaux:
    Level 1-2: PerceptionAgent - Collecte et normalisation
    Level 3:   AnalysisAgent - Analyse et scoring risque
    Level 4:   DecisionAgent - Recommandations d'intervention
    Level 5:   OrchestratorAgent - Coordination et exécution

Communication inter-agents via messages asynchrones.
"""

from robosafe.agents.base_agent import (
    BaseAgent,
    AgentConfig,
    AgentLevel,
    AgentState,
    AgentMessage,
    MessagePriority,
)

from robosafe.agents.perception_agent import (
    PerceptionAgent,
    PerceptionConfig,
    NormalizedSignal,
    SignalQuality,
)

from robosafe.agents.analysis_agent import (
    AnalysisAgent,
    AnalysisConfig,
    RiskScore,
    RiskLevel,
    PatternAlert,
)

from robosafe.agents.decision_agent import (
    DecisionAgent,
    DecisionConfig,
    ActionRecommendation,
    ActionType,
    ActionUrgency,
)

from robosafe.agents.orchestrator_agent import (
    OrchestratorAgent,
    OrchestratorConfig,
    ExecutionRecord,
    ExecutionStatus,
)

__all__ = [
    # Base
    "BaseAgent",
    "AgentConfig",
    "AgentLevel",
    "AgentState",
    "AgentMessage",
    "MessagePriority",
    
    # Perception (L1-L2)
    "PerceptionAgent",
    "PerceptionConfig",
    "NormalizedSignal",
    "SignalQuality",
    
    # Analysis (L3)
    "AnalysisAgent",
    "AnalysisConfig",
    "RiskScore",
    "RiskLevel",
    "PatternAlert",
    
    # Decision (L4)
    "DecisionAgent",
    "DecisionConfig",
    "ActionRecommendation",
    "ActionType",
    "ActionUrgency",
    
    # Orchestrator (L5)
    "OrchestratorAgent",
    "OrchestratorConfig",
    "ExecutionRecord",
    "ExecutionStatus",
]
