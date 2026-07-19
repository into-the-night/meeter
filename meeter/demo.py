from __future__ import annotations

from .domain import ActionItem, Meeting, TranscriptTurn


def demo_meeting() -> Meeting:
    transcript = [
        TranscriptTurn(0, 18, "Maya Chen", "Thanks, everyone. The main goal today is to lock the rollout plan for the merchant insights beta. We need a decision on scope and owners before Friday."),
        TranscriptTurn(19, 43, "Arjun Mehta", "Engineering can ship the dashboard and weekly digest in the first cut. The anomaly alerts need another sprint because the thresholds still create too many false positives."),
        TranscriptTurn(44, 64, "Unknown speaker 1", "From the pilot calls, the digest is the strongest hook. I would rather launch with two polished workflows than three uneven ones."),
        TranscriptTurn(65, 87, "Maya Chen", "Agreed. Let's remove alerts from beta scope. Arjun, can you publish the revised technical plan by Tuesday? Include the event schema dependency."),
        TranscriptTurn(88, 112, "Arjun Mehta", "Yes. I also need Finance to confirm the warehouse cost ceiling. If we do not have that by Wednesday, we should cap digest history at ninety days."),
        TranscriptTurn(113, 139, "Leena Rao", "I will get the cost ceiling from Finance by Wednesday noon. For customer communication, I can draft the pilot email and the in-product copy by Thursday."),
        TranscriptTurn(140, 165, "Unknown speaker 1", "I will schedule five merchant validation calls for next week. We should explicitly test whether weekly or daily delivery feels more useful."),
        TranscriptTurn(166, 188, "Maya Chen", "Perfect. Decision is dashboard plus weekly digest for beta, twenty pilot merchants, and no anomaly alerts. Let's review readiness next Friday at eleven."),
    ]
    return Meeting(
        title="Merchant Insights — Beta rollout",
        duration=188,
        transcript=transcript,
        summary="The team aligned on a focused beta for Merchant Insights: a dashboard and weekly digest for 20 pilot merchants. Anomaly alerts will move to a later sprint so the first release can prioritize quality and clear customer value.",
        decisions=[
            "Launch the beta with the dashboard and weekly digest only.",
            "Limit the pilot to 20 merchants and defer anomaly alerts.",
            "Use a 90-day digest history cap if warehouse cost approval is delayed.",
        ],
        actions=[
            ActionItem("Publish the revised technical plan with event schema dependencies", "Arjun Mehta", "2026-07-21", "high", "Required to keep the beta build on schedule."),
            ActionItem("Confirm the warehouse cost ceiling with Finance", "Leena Rao", "2026-07-22", "high", "Determines whether digest history can exceed 90 days."),
            ActionItem("Draft the pilot email and in-product launch copy", "Leena Rao", "2026-07-23", "medium", "Prepare communication for the 20 pilot merchants."),
            ActionItem("Schedule five merchant validation calls", "Unknown speaker 1", "2026-07-24", "medium", "Test weekly versus daily digest preference."),
        ],
        discussion=[
            {"title": "Beta scope", "detail": "Dashboard and digest are ready; anomaly detection thresholds need another sprint."},
            {"title": "Cost dependency", "detail": "Warehouse cost approval affects how much digest history can be retained."},
            {"title": "Customer learning", "detail": "Validation calls will compare weekly and daily delivery preferences."},
        ],
        risks=["Warehouse cost ceiling may not be confirmed before implementation locks.", "Unknown speaker 1 has not yet been identified or enrolled."],
        participants=[
            {"id": "maya", "name": "Maya Chen", "known": True, "color": "violet"},
            {"id": "arjun", "name": "Arjun Mehta", "known": True, "color": "blue"},
            {"id": "leena", "name": "Leena Rao", "known": True, "color": "green"},
            {"id": "unknown_1", "name": "Unknown speaker 1", "known": False, "color": "amber"},
        ],
        source_name="Merchant-insights-sync.m4a",
        model_note="Demo data · designed for local processing",
    )
