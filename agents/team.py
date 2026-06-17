"""
Ghost Requirement Agent - Agno Team Orchestration
=================================================
Architecture:
  Celery (Slack queue) → GhostRequirementTeam.run() → Agno Team (coordinate mode)
                                                           ├─ Agent 1: IngestionFilterAgent
                                                           ├─ Agent 2: BacklogReconcilerAgent  
                                                           └─ Agent 3: ConflictResolverAgent

Celery is ONLY responsible for:
  - Receiving Slack/Teams messages from the ingestion queue
  - Persisting raw messages to PostgreSQL
  - Calling the Agno Team to run the full pipeline
  - Persisting the structured result back to PostgreSQL
  - Handling retries & dead-letter routing

Agno Team is responsible for:
  - Agent 1: Noise filtering + requirement extraction (Gemini 2.5 Flash)
  - Agent 2: Embedding + pgvector similarity search (gemini-embedding-001)  
  - Agent 3: Contradiction detection or Jira ticket drafting (Gemini 2.5 Flash)
  - Coordinating the full pipeline as a sequential multi-agent flow
"""

import json
import logging
from typing import Optional, List
from pydantic import BaseModel, Field

from agno.agent import Agent
from agno.team import Team
from agno.team.mode import TeamMode
from agno.models.google import Gemini

from agents.config import GEMINI_API_KEY
from agents.embedding import get_embedding, get_query_embedding

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Schemas (Structured Outputs for each Agent)
# ─────────────────────────────────────────────────────────────────────────────

class RequirementExtraction(BaseModel):
    """Agent 1 structured output — FR-02 & FR-03."""
    isRequirement: bool = Field(
        description=(
            "True if the message contains a product requirement, feature request, "
            "design constraint, technical specification, deadline change, or priority change. "
            "False for casual chat, greetings, social coordination, or status updates."
        )
    )
    requirementText: str = Field(
        description=(
            "The extracted, sanitized requirement statement. "
            "Preserve exact technical details (hex colors, timeframes, component names). "
            "Empty string if isRequirement=False."
        )
    )
    isHardConstraint: bool = Field(
        description=(
            "True if mandatory (uses 'must', 'shall', 'required', deadlines, compliance). "
            "False for soft suggestions ('should', 'could', 'nice-to-have')."
        )
    )
    confidenceScore: float = Field(
        description="Confidence 0.0-1.0 that this is a genuine product requirement."
    )
    rationale: str = Field(
        description="One sentence explaining the classification decision."
    )


class SlackAttribution(BaseModel):
    channel: str = Field(description="Slack channel where the requirement originated")
    author: str = Field(description="Slack user who stated the requirement")
    timestamp: str = Field(description="ISO timestamp of the Slack message")


class TicketDraft(BaseModel):
    """Jira-compatible ticket draft — FR-07."""
    title: str = Field(description="Clear, action-oriented Jira ticket title (max 100 chars)")
    description: str = Field(description="Detailed description with context and constraints")
    acceptanceCriteria: List[str] = Field(
        description="Acceptance criteria in strict 'Given [context] When [action] Then [outcome]' format. Minimum 2 criteria."
    )
    components: List[str] = Field(
        description="Affected system components (e.g. ['Frontend', 'Auth Service'])"
    )
    slackAttribution: SlackAttribution


class ConflictResolution(BaseModel):
    """Agent 3 dual-branch output — FR-06 & FR-07."""
    resolution_type: str = Field(
        description="Exactly 'contradiction_detected' or 'create_new_ticket'."
    )
    conflict_analysis: Optional[str] = Field(
        None,
        description=(
            "Branch A: Markdown conflict analysis with side-by-side comparison table "
            "and recommended resolution. Only set when resolution_type='contradiction_detected'."
        )
    )
    suggested_ticket_draft: Optional[TicketDraft] = Field(
        None,
        description=(
            "Branch B: Complete Jira ticket draft. "
            "Only set when resolution_type='create_new_ticket'."
        )
    )


class PipelineResult(BaseModel):
    """Final output returned by the Agno Team after the full pipeline run."""
    is_requirement: bool
    requirement_text: str
    is_hard_constraint: bool
    confidence_score: float
    rationale: str
    resolution_type: Optional[str] = None        # 'create_new_ticket' | 'contradiction_detected' | 'exact_match_found' | None
    similarity_score: Optional[float] = None
    closest_ticket_id: Optional[str] = None
    conflict_analysis: Optional[str] = None
    # JSON-serialised TicketDraft (str avoids Gemini schema rejecting additionalProperties on dict)
    suggested_ticket_draft: Optional[str] = None


class PRViolation(BaseModel):
    requirement_id: str = Field(description="UUID of the violated requirement")
    requirement_text: str = Field(description="Text of the requirement that was violated")
    file_path: str = Field(description="File path in the PR diff where the violation occurs")
    explanation: str = Field(description="Detailed explanation of the violation, highlighting the lines/files and how to fix it to comply")


class PRAuditResult(BaseModel):
    status: str = Field(description="Must be 'compliant' if no violations are found, or 'violations_flagged' if any requirement is violated")
    violations: List[PRViolation] = Field(default=[], description="List of detected requirement violations")
    summary: str = Field(description="Overall summary of the PR diff audit")


# ─────────────────────────────────────────────────────────────────────────────
# Agent 1: Ingestion Filter (Gemini 2.5 Flash)
# FR-02: <5% false positive | FR-03: Structured extraction
# ─────────────────────────────────────────────────────────────────────────────

def build_ingestion_filter_agent() -> Agent:
    """
    Agent 1 — Noise filter and requirement extractor.
    
    Role in Team: Receives the raw Slack/Teams message from the coordinator,
    classifies it, and returns a RequirementExtraction JSON.
    The Team coordinator uses this result to decide whether to proceed
    to Agents 2 & 3 or terminate the pipeline early (noise filtered).
    """
    return Agent(
        id="ingestion-filter",
        name="Ingestion Filter",
        role=(
            "Classify Slack/Teams messages and extract product requirements. "
            "Return structured JSON with isRequirement, requirementText, isHardConstraint, "
            "confidenceScore, and rationale."
        ),
        model=Gemini(id="gemini-2.5-flash", api_key=GEMINI_API_KEY),
        instructions=[
            "You are the Ghost Ingestion Filter — an expert classifier for a software product team.",
            "",
            "## Classify as isRequirement=True:",
            "- Feature specifications: 'The login button MUST use color #333 in dark mode'",
            "- Technical constraints: 'Session cookies must expire after 30 minutes'",
            "- Design decisions with specifics: 'On mobile, center the CTA button'",
            "- Timeline/deadlines: '2FA must ship by end of Q3'",
            "- Priority changes: 'Payment flow refactor is now P0'",
            "- Architecture decisions: 'Migrating auth to OAuth 2.0'",
            "- Compliance/regulatory: 'GDPR requires user data deletion within 30 days'",
            "",
            "## Classify as isRequirement=False (noise):",
            "- Greetings: 'GM everyone!', 'How is the sprint going?'",
            "- Vague opinions: 'The UI looks ugly', 'Make it better'",
            "- Status updates: 'The build is green', 'PR merged'",
            "- Social coordination: 'Lunch at 1pm?', 'OOO tomorrow'",
            "- Acknowledgements: 'LGTM', 'Sounds good', 'Got it'",
            "",
            "## Rules:",
            "1. Be conservative — prefer isRequirement=False when genuinely ambiguous",
            "2. Preserve exact technical details (hex codes, percentages, dates, numbers)",
            "3. isHardConstraint=True only for 'must', 'shall', 'required', deadlines, compliance",
            "4. Always return valid JSON matching the RequirementExtraction schema",
        ],
        output_schema=RequirementExtraction,
        structured_outputs=True,
        markdown=False,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Agent 3: Conflict Resolver (Gemini 2.5 Flash)
# FR-06: Contradiction detection | FR-07: Ticket drafting
# ─────────────────────────────────────────────────────────────────────────────

def build_conflict_resolver_agent() -> Agent:
    """
    Agent 3 — Conflict resolver and Jira ticket drafter.
    
    Role in Team: Receives the extracted requirement + top similar backlog tickets
    (provided by the coordinator from Agent 2's embedding results), then decides:
    - Branch A: contradiction_detected → detailed conflict analysis
    - Branch B: create_new_ticket → full Jira-compatible ticket draft
    """
    return Agent(
        id="conflict-resolver",
        name="Conflict Resolver",
        role=(
            "Analyze a new requirement against existing backlog tickets to detect contradictions "
            "or draft a new Jira ticket. Return structured JSON with resolution_type, "
            "conflict_analysis (if contradiction), or suggested_ticket_draft (if new ticket)."
        ),
        model=Gemini(id="gemini-2.5-flash", api_key=GEMINI_API_KEY),
        instructions=[
            "You are the Ghost Conflict Resolver — a senior engineering strategist.",
            "",
            "## Use 'contradiction_detected' when:",
            "- The new requirement DIRECTLY contradicts an existing ticket specification",
            "- Example: New='login button must be blue', Existing='login button must be green'",
            "- Two requirements cannot both be true simultaneously",
            "",
            "## Use 'create_new_ticket' when:",
            "- Genuinely new feature not in backlog",
            "- Adds detail without conflicting",
            "- Similarity score < 0.65 (no meaningful overlap)",
            "",
            "## Branch A output format (contradiction_detected):",
            "Write conflict_analysis as structured markdown:",
            "```",
            "## Contradiction Detected",
            "**New Requirement:** [exact text]",
            "**Conflicting Ticket:** [ID]: [title]",
            "",
            "| Aspect | New Requirement | Existing Ticket |",
            "|--------|----------------|----------------|",
            "| [key point] | [new spec] | [existing spec] |",
            "",
            "**Root Cause:** [why this conflicts]",
            "**Recommended Resolution:** [specific steps]",
            "```",
            "",
            "## Branch B output format (create_new_ticket):",
            "- Clear, action-oriented title (max 100 chars)",
            "- Detailed description with context",
            "- At least 3 acceptance criteria in STRICT 'Given [context] When [action] Then [outcome]' format",
            "- Relevant components list",
            "",
            "## Rules:",
            "1. resolution_type must be EXACTLY 'contradiction_detected' or 'create_new_ticket'",
            "2. If contradiction: set conflict_analysis, leave suggested_ticket_draft null",
            "3. If new ticket: set suggested_ticket_draft, leave conflict_analysis null",
            "4. High similarity (0.65-0.85) does NOT automatically mean contradiction — check actual content",
        ],
        output_schema=ConflictResolution,
        structured_outputs=True,
        markdown=False,
    )


# ─────────────────────────────────────────────────────────────────────────────
# GhostRequirementTeam — Agno Team (coordinate mode)
# ─────────────────────────────────────────────────────────────────────────────

class GhostRequirementTeam:
    """
    Agno Team orchestrating the 3-agent Ghost requirement pipeline.
    
    Mode: TeamMode.coordinate
      - The coordinator (Gemini 2.5 Flash) decides which agents to invoke
        and in what order, based on intermediate results.
      - Pipeline:
          1. Coordinator sends raw message to Agent 1 (IngestionFilter)
          2. If isRequirement=True, coordinator calls get_backlog_similarity tool
          3. Based on similarity score, coordinator sends context to Agent 3
          4. Coordinator synthesizes final PipelineResult JSON
    
    Celery integration:
      - Celery task calls GhostRequirementTeam().run(message_text, metadata)
      - Returns a PipelineResult dict that Celery persists to PostgreSQL
    """

    def __init__(self):
        self._team = self._build_team()

    def _build_team(self) -> Team:
        """Build the Agno coordinate-mode team with all 3 agents."""
        
        ingestion_agent = build_ingestion_filter_agent()
        resolver_agent = build_conflict_resolver_agent()

        team = Team(
            name="Ghost Requirement Team",
            mode=TeamMode.coordinate,
            model=Gemini(id="gemini-2.5-flash", api_key=GEMINI_API_KEY),
            members=[ingestion_agent, resolver_agent],
            instructions=[
                "You are the Ghost Requirement Team coordinator. Your job is to process "
                "Slack/Teams messages through the requirement pipeline and return a structured result.",
                "",
                "## Pipeline Steps:",
                "",
                "### Step 1 — Classify the message:",
                "Delegate to 'Ingestion Filter' with the raw message text.",
                "If isRequirement=False, return immediately with is_requirement=false.",
                "",
                "### Step 2 — Similarity search:",
                "The backlog similarity results are already provided in the prompt context.",
                "Use those scores directly — do NOT try to call any external tool.",
                "",
                "### Step 3 — Route based on similarity:",
                "- score >= 0.85: Exact match. Return resolution_type='exact_match_found'. No Agent 3 needed.",
                "- score 0.65-0.85: Conflict zone. Delegate to 'Conflict Resolver' with the requirement + ticket context.",
                "- score < 0.65: New discovery. Delegate to 'Conflict Resolver' to draft a new ticket.",
                "",
                "### Step 4 — Return final result:",
                "IMPORTANT: Your FINAL response must be a raw JSON object (no markdown fences, no explanatory text).",
                "The JSON must contain exactly these fields:",
                '{"is_requirement": bool, "requirement_text": str, "is_hard_constraint": bool, '
                '"confidence_score": float, "rationale": str, "resolution_type": str|null, '
                '"similarity_score": float|null, "closest_ticket_id": str|null, '
                '"conflict_analysis": str|null, "suggested_ticket_draft": str|null}',
                "",
                "## Critical Rules:",
                "- suggested_ticket_draft must be a JSON STRING (not a nested object) serializing the ticket fields",
                "- conflict_analysis is a markdown string explaining the contradiction",
                "- Return ONLY the JSON object as your final message — nothing else",
            ],
            stream_member_events=False,
            markdown=False,
        )

        return team

    def run(
        self,
        message_text: str,
        channel: str = "unknown",
        author: str = "unknown",
        timestamp: str = "",
        backlog_context: str = "",
    ) -> PipelineResult:
        """
        Run the full Ghost pipeline for a single Slack/Teams message.
        
        Called by the Celery ingestion task. The caller passes in the
        pre-fetched backlog_context (top similar tickets as formatted text)
        so the embedding search happens outside the LLM loop.
        
        Args:
            message_text:    Raw text from Slack/Teams
            channel:         Source channel identifier
            author:          User handle / ID
            timestamp:       ISO timestamp of the original message
            backlog_context: Pre-formatted string with top similar tickets + scores
            
        Returns:
            PipelineResult with extraction + reconciliation decision
        """
        prompt = self._build_prompt(message_text, channel, author, timestamp, backlog_context)

        logger.info(f"[GhostTeam] Running pipeline for message: '{message_text[:60]}...'")

        response = self._team.run(prompt)
        raw = response.content

        # Agno may return a PipelineResult directly, a JSON string, or a
        # plain text reply containing a fenced JSON block.
        if isinstance(raw, PipelineResult):
            result = raw
        elif isinstance(raw, dict):
            result = PipelineResult(**raw)
        elif isinstance(raw, str):
            import re as _re, json as _json
            # Strip markdown code fences if present
            clean = _re.sub(r"```(?:json)?\s*", "", raw, flags=_re.IGNORECASE).strip().rstrip("`").strip()
            try:
                result = PipelineResult(**_json.loads(clean))
            except Exception:
                # Final fallback — treat as noise (non-requirement)
                logger.warning("[GhostTeam] Could not parse team response as PipelineResult — treating as non-requirement")
                result = PipelineResult(
                    is_requirement=False,
                    requirement_text="",
                    is_hard_constraint=False,
                    confidence_score=0.0,
                    rationale=f"Parse error: {str(raw)[:120]}",
                )
        else:
            logger.warning(f"[GhostTeam] Unexpected response type: {type(raw)}")
            result = PipelineResult(
                is_requirement=False,
                requirement_text="",
                is_hard_constraint=False,
                confidence_score=0.0,
                rationale="Unexpected response type from Team",
            )

        logger.info(
            f"[GhostTeam] Pipeline complete: is_requirement={result.is_requirement} "
            f"resolution={result.resolution_type} similarity={result.similarity_score}"
        )
        return result

    @staticmethod
    def _build_prompt(
        message_text: str,
        channel: str,
        author: str,
        timestamp: str,
        backlog_context: str,
    ) -> str:
        """Build the structured prompt for the coordinator."""
        lines = [
            "## Raw Message to Process",
            f'"{message_text}"',
            "",
            "## Slack Metadata",
            f"- Channel: {channel}",
            f"- Author: {author}",
            f"- Timestamp: {timestamp}",
        ]

        if backlog_context:
            lines += [
                "",
                "## Pre-fetched Backlog Similarity Results",
                "(These were generated via gemini-embedding-001 + pgvector cosine search)",
                backlog_context,
            ]
        else:
            lines += [
                "",
                "## Backlog Similarity Results",
                "No backlog tickets found. Treat as new discovery if classified as a requirement.",
            ]

        lines += [
            "",
            "## Your Task",
            "1. Delegate to 'Ingestion Filter' to classify this message.",
            "2. If it IS a requirement, use the backlog similarity results above to route:",
            "   - similarity >= 0.85 → exact_match_found (no Agent 3 needed)",
            "   - similarity 0.65-0.85 → delegate to 'Conflict Resolver' for contradiction check",
            "   - similarity < 0.65 → delegate to 'Conflict Resolver' to draft a new Jira ticket",
            "3. Return the final PipelineResult JSON.",
        ]

        return "\n".join(lines)


def build_pr_auditor_agent() -> Agent:
    """
    Agent 4 (PR Auditor) — Analyzes code diffs against active requirements.
    
    Checks if pull request modifications violate any database constraints.
    """
    return Agent(
        id="pr-auditor",
        name="PR Auditor",
        role=(
            "Audit a pull request code diff against a set of relevant product/system requirements. "
            "Detect if the code changes violate, override, or fail to implement the requirements. "
            "Return a structured PRAuditResult JSON."
        ),
        model=Gemini(id="gemini-2.5-flash", api_key=GEMINI_API_KEY),
        instructions=[
            "You are the Ghost PR Auditor — a senior staff engineer and security/compliance reviewer.",
            "Your task is to analyze a Git pull request diff against a list of active product requirements.",
            "",
            "## Your Input:",
            "1. PR Metadata: Title, repo name, PR number.",
            "2. PR Diff: The unified diff showing code additions and deletions.",
            "3. Candidate Requirements: A list of active product requirements/constraints (with their IDs) that may apply to this pull request.",
            "",
            "## Audit Instructions:",
            "- Compare the added/modified code lines in the diff against each candidate requirement.",
            "- Flag a violation if the diff implements logic that directly contradicts a requirement.",
            "  - Example: A requirement states 'login button MUST use color #1A73E8 on mobile', but the diff modifies mobile styles to use background: red or #FF0000.",
            "  - Example: A requirement states 'session timeout MUST be 15 minutes', but the diff modifies session config/env settings to 30 * 60 or '40m'.",
            "- If the PR changes code in a file that seems related to a requirement but conforms to it, DO NOT flag it.",
            "- Return status='compliant' with an empty violations list if no issues are detected.",
            "- Return status='violations_flagged' and populate the violations list with detailed explanations if one or more requirements are violated.",
            "- Always return valid JSON matching the PRAuditResult schema.",
        ],
        output_schema=PRAuditResult,
        structured_outputs=True,
        markdown=False,
    )

