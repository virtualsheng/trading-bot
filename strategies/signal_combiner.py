def combine_signals(sentiment_signal: dict, technical_signal: dict) -> dict:
    """
    Combine Jeff's sentiment signal with technical signal.
    Only upgrades confidence when both agree.
    """
    s_action = sentiment_signal.get("action", "HOLD")
    t_action = technical_signal.get("action", "HOLD")
    s_confidence = sentiment_signal.get("confidence", 0)
    t_strength = technical_signal.get("strength", "WEAK")
    
    strength_boost = {"STRONG": 20, "MODERATE": 10, "WEAK": 0}
    
    if s_action == t_action and s_action != "HOLD":
        # Both agree — boost confidence
        boost = strength_boost[t_strength]
        combined_confidence = min(s_confidence + boost, 99)
        combined_action = s_action
        agreement = "CONFIRMED"
    elif s_action == "HOLD" and t_action != "HOLD":
        # Technicals lead, sentiment neutral — weak signal
        combined_confidence = 30
        combined_action = t_action
        agreement = "TECHNICAL_ONLY"
    elif t_action == "HOLD" and s_action != "HOLD":
        # Sentiment leads, technicals neutral
        combined_confidence = s_confidence
        combined_action = s_action
        agreement = "SENTIMENT_ONLY"
    else:
        # Conflict — stand down
        combined_confidence = 0
        combined_action = "HOLD"
        agreement = "CONFLICT"
    
    return {
        "action": combined_action,
        "confidence": combined_confidence,
        "agreement": agreement,
        "sentiment_action": s_action,
        "technical_action": t_action,
        "technical_strength": t_strength,
        "execute": combined_confidence >= 75 and agreement == "CONFIRMED"
    }