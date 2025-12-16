"""
RoboSafe Sentinel - Système de sécurité intelligent pour cellules robotisées.

Ce module fournit un système de supervision de sécurité basé sur l'IA
pour les cellules robotisées industrielles, compatible avec l'architecture AgenticX5.

Architecture:
    - Niveau 1: Collecte (signaux temps réel)
    - Niveau 2: Normalisation (fusion capteurs)
    - Niveau 3: Analyse (évaluation risques)
    - Niveau 4: Recommandation (décisions intervention)
    - Niveau 5: Orchestration (coordination globale)

Conformité:
    - ISO 10218-1/-2:2025
    - ISO/TS 15066
    - ISO 13849-1 (PL d/e)
    - IEC 62443 (cybersécurité)

Copyright (c) 2024-2025 Preventera / GenAISafety
"""

__version__ = "0.1.0"
__author__ = "Preventera / GenAISafety"
__license__ = "Proprietary"

from robosafe.core.state_machine import SafetyState, SafetyStateMachine
from robosafe.core.signal_manager import SignalManager
from robosafe.core.rule_engine import RuleEngine

__all__ = [
    "__version__",
    "SafetyState",
    "SafetyStateMachine",
    "SignalManager",
    "RuleEngine",
]
