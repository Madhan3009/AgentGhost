"""
Ghost Requirement Agent - LLM Layer
Defines Agno agents and embedding utilities for the pipeline.

Agent 1: Ingestion Filter (Gemini 2.5 Flash) - Noise filtering + JSON extraction
Agent 3: Conflict Resolver (Gemini 2.5 Flash) - Contradiction + ticket drafting
Agent 2: Embeddings (text-embedding-004 via google-genai) - 768-dim vectors
"""
import os
import logging
from pydantic import BaseModel, Field
from typing import List, Optional
from google import genai
from google.genai import types
from agno.agent import Agent
from agno.models.google import Gemini
from agents.config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Output Schemas
# ─────────────────────────────────────────────────────────────────────────────

class RequirementExtraction(BaseModel):
    """
    Agent 1 (Ingestion Filter) structured output.
    FR-02: <5% false positive rate on casual/social messages
    FR-03: Extract text, isHardConstraint, confidenceScore, rationale
    """
    isRequirement: bool = Field(
        description=(
            "True if the message contains a product requirement, feature request, "
            "design constraint, technical specification, deadline, or priority change. "
            "False for casual chat, greetings, emojis, social coordination, stand-up check-ins, "
            "general questions with no actionable spec, or noise."
        )
    )
    requirementText: str = Field(
        description=(
            "The extracted and sanitized requirement statement. If isRequirement=False, "
            "return an empty string. Preserve the intent and specifics without paraphrasing "
            "away critical details like color codes, timeframes, or component names."
        )
    )
    isHardConstraint: bool = Field(
        description=(
            "True if this is a strict/mandatory rule (e.g., 'must', 'shall', 'required', "
            "'deadline is', legal/compliance requirements). "
            "False if it's a suggestion, preference, nice-to-have, or open-ended discussion."
        )
    )
    confidenceScore: float = Field(
        description=(
            "Confidence score between 0.0 and 1.0 on whether this is truly a requirement. "
            "High (>0.85) for clear specs. Medium (0.5-0.85) for ambiguous messages. "
            "Low (<0.5) for likely noise."
        )
    )
    rationale: str = Field(
        description=(
            "One to two sentence explanation of why this was or wasn't classified as a requirement, "
            "referencing specific words or phrases from the message."
        )
    )


class SlackAttribution(BaseModel):
    """Slack source attribution for ticket drafts."""
    channel: str = Field(description="Slack channel name or ID where the requirement originated")
    author: str = Field(description="Slack user ID or display name of the person who stated the requirement")
    timestamp: str = Field(description="ISO timestamp of the Slack message")


class TicketDraft(BaseModel):
    """
    Jira-compatible ticket draft (Agent 3, Branch B output).
    FR-07: Given/When/Then acceptance criteria format.
    """
    title: str = Field(description="A clear, action-oriented Jira ticket title (max 100 chars)")
    description: str = Field(
        description=(
            "Detailed description of the feature or requirement including background context, "
            "affected components, and any technical constraints mentioned."
        )
    )
    acceptanceCriteria: List[str] = Field(
        description=(
            "Acceptance criteria written strictly in 'Given [context] When [action] Then [outcome]' format. "
            "Provide at least 2-3 criteria covering the happy path and edge cases."
        )
    )
    components: List[str] = Field(
        description="List of system components, services, or areas affected (e.g., ['Frontend', 'Auth Service', 'Mobile'])"
    )
    slackAttribution: SlackAttribution = Field(
        description="Attribution metadata linking this ticket back to the original Slack message"
    )


class ConflictResolution(BaseModel):
    """
    Agent 3 (Conflict Resolver) structured output.
    Dual-branch: contradiction analysis OR new ticket draft.
    """
    resolution_type: str = Field(
        description=(
            "Must be exactly one of: 'contradiction_detected' or 'create_new_ticket'. "
            "Use 'contradiction_detected' when the new requirement directly conflicts with an existing ticket. "
            "Use 'create_new_ticket' when the requirement is genuinely new or additive."
        )
    )
    conflict_analysis: Optional[str] = Field(
        None,
        description=(
            "Branch A (contradiction_detected only): Detailed markdown analysis showing:\n"
            "1. What the new requirement states\n"
            "2. What the existing ticket states\n"
            "3. The specific contradiction or conflict\n"
            "4. Recommended resolution strategy\n"
            "Leave null if resolution_type is 'create_new_ticket'."
        )
    )
    suggested_ticket_draft: Optional[TicketDraft] = Field(
        None,
        description=(
            "Branch B (create_new_ticket only): Complete Jira-compatible ticket draft. "
            "Leave null if resolution_type is 'contradiction_detected'."
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# Agent 2: Embedding Generator (text-embedding-004, 768-dim)
# FR-04: Generate 768-dim vectors via text-embedding-004
# ─────────────────────────────────────────────────────────────────────────────

def get_embedding(text: str) -> List[float]:
    """
    Generate a 768-dimensional vector embedding using Google's text-embedding-004 model.
    Used by Agent 2 (Backlog Reconciler) for cosine similarity matching.
    
    Args:
        text: The requirement text or ticket content to embed
        
    Returns:
        List of 768 float values representing the semantic embedding
    """
    if not GEMINI_API_KEY:
        raise ValueError(
            "GEMINI_API_KEY is not configured. Set it in .env or environment variables."
        )
    
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    # Use gemini-embedding-001 — confirmed 768-dim support with this API key
    # (text-embedding-004 maps to the same model via the v1beta endpoint)
    response = client.models.embed_content(
        model="gemini-embedding-001",
        contents=text,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_DOCUMENT",
            output_dimensionality=768
        )
    )
    
    return response.embeddings[0].values


def get_query_embedding(text: str) -> List[float]:
    """
    Generate a query embedding for similarity search (RETRIEVAL_QUERY task type).
    Used when searching the backlog_index for similar tickets.
    """
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not configured.")
    
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    response = client.models.embed_content(
        model="gemini-embedding-001",
        contents=text,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=768
        )
    )
    
    return response.embeddings[0].values


# ─────────────────────────────────────────────────────────────────────────────
# Agent 1: Ingestion Filter (Gemini 2.5 Flash)
# FR-02: <5% false positive rate | FR-03: Structured JSON extraction
# ─────────────────────────────────────────────────────────────────────────────

def get_extraction_agent() -> Agent:
    """
    Agent 1: Ingestion Filter and Requirement Extractor.
    
    Classifies incoming Slack/Teams messages to determine if they contain
    product requirements. Uses Gemini 2.5 Flash with structured output
    to ensure consistent JSON schema compliance.
    """
    return Agent(
        name="Ghost Ingestion Filter",
        model=Gemini(id="gemini-2.5-flash", api_key=GEMINI_API_KEY),
        instructions=(
            "You are the Ghost Ingestion Filter — an expert Requirement Extraction agent for a software product team.\n\n"
            
            "## Your Mission\n"
            "Analyze messages from Slack/Teams channels and determine if they contain actionable product requirements.\n\n"
            
            "## What IS a Requirement (classify as isRequirement=True):\n"
            "- Feature specifications or requests: 'The login button MUST use color #333 in dark mode'\n"
            "- Technical constraints: 'Session cookies must expire after 30 minutes'\n"
            "- Design decisions with specifics: 'On mobile, center the CTA button'\n"
            "- Timeline/deadline requirements: '2FA must ship by end of Q3'\n"
            "- Priority changes: 'The payment flow refactor is now P0'\n"
            "- Architecture decisions: 'We are migrating auth to OAuth 2.0'\n"
            "- Compliance/regulatory rules: 'GDPR requires user data deletion within 30 days'\n\n"
            
            "## What is NOT a Requirement (classify as isRequirement=False):\n"
            "- Casual greetings: 'GM everyone!', 'How's the sprint going?'\n"
            "- General questions without specs: 'Should we do dark mode?', 'What do you think?'\n"
            "- Status updates without specs: 'The build is green', 'PR merged'\n"
            "- Social coordination: 'Lunch at 1pm?', 'OOO tomorrow'\n"
            "- Vague preferences without detail: 'The UI looks ugly', 'Make it better'\n"
            "- Acknowledgements: 'LGTM', 'Sounds good', 'Got it'\n\n"
            
            "## Hard Constraint vs Soft Constraint:\n"
            "- HARD: Uses 'must', 'shall', 'required', 'cannot', 'mandatory', specific deadline dates, compliance/legal needs\n"
            "- SOFT: Uses 'should', 'could', 'nice to have', 'ideally', 'when possible'\n\n"
            
            "## Critical Rules:\n"
            "1. Be conservative — when in doubt, prefer isRequirement=False to avoid false positives\n"
            "2. Preserve exact technical details (hex colors, percentages, dates, component names)\n"
            "3. If a message is clearly noise, still return a valid JSON with isRequirement=False\n"
            "4. Never include PII (email addresses, phone numbers) in requirementText"
        ),
        output_schema=RequirementExtraction,
        structured_outputs=True,
        markdown=False
    )


# ─────────────────────────────────────────────────────────────────────────────
# Agent 3: Conflict Resolver (Gemini 2.5 Flash)
# FR-06: Contradiction detection | FR-07: Jira ticket drafting
# ─────────────────────────────────────────────────────────────────────────────

def get_resolver_agent() -> Agent:
    """
    Agent 3: Backlog Conflict Resolver and Ticket Drafter.
    
    Dual-branch logic:
    - Branch A (contradiction_detected): Deep semantic comparison with conflict analysis
    - Branch B (create_new_ticket): High-quality Jira ticket draft in GWT format
    """
    return Agent(
        name="Ghost Conflict Resolver",
        model=Gemini(id="gemini-2.5-flash", api_key=GEMINI_API_KEY),
        instructions=(
            "You are the Ghost Conflict Resolver — a senior engineering strategist who analyzes new product requirements "
            "against an existing backlog to detect contradictions and draft high-quality tickets.\n\n"
            
            "## Your Input\n"
            "You will receive:\n"
            "1. A new requirement extracted from Slack/Teams\n"
            "2. The top 3 most similar tickets from the existing backlog (with similarity scores)\n"
            "3. The similarity score of the closest match\n\n"
            
            "## Decision Logic\n\n"
            "### Use 'contradiction_detected' when:\n"
            "- The new requirement directly contradicts an existing ticket specification\n"
            "  - Example: New says 'login button must be blue', existing says 'login button must be green'\n"
            "  - Example: New says 'session expires in 15 minutes', existing says '30 minutes'\n"
            "- Two requirements cannot both be true simultaneously\n"
            "- The new requirement reverses or overrides a previously decided spec\n\n"
            
            "### Use 'create_new_ticket' when:\n"
            "- The requirement covers a genuinely new feature not in the backlog\n"
            "- It adds additional detail to an existing area without contradicting it\n"
            "- The similarity score is low (<0.65) indicating no overlap\n"
            "- Related tickets exist but there's no actual conflict\n\n"
            
            "## Branch A Output (contradiction_detected):\n"
            "Write a detailed conflict_analysis with:\n"
            "```\n"
            "## Contradiction Detected\n"
            "**New Requirement:** [exact text]\n"
            "**Conflicting Ticket:** [TICKET-ID]: [title]\n\n"
            "| Aspect | New Requirement | Existing Ticket |\n"
            "|--------|----------------|----------------|\n"
            "| [key point] | [new spec] | [existing spec] |\n\n"
            "**Root Cause:** [why this conflicts]\n"
            "**Recommended Resolution:** [specific actionable steps]\n"
            "```\n\n"
            
            "## Branch B Output (create_new_ticket):\n"
            "Draft a professional Jira ticket with:\n"
            "- Clear, action-oriented title\n"
            "- Detailed description with context\n"
            "- At least 3 acceptance criteria in STRICT Given/When/Then format:\n"
            "  'Given [precondition], When [action], Then [expected outcome]'\n"
            "- Relevant components list\n"
            "- Slack attribution metadata\n\n"
            
            "## Critical Rules:\n"
            "1. resolution_type must be exactly 'contradiction_detected' or 'create_new_ticket'\n"
            "2. If contradiction: set conflict_analysis, leave suggested_ticket_draft as null\n"
            "3. If new ticket: set suggested_ticket_draft, leave conflict_analysis as null\n"
            "4. High similarity (0.65-0.85) doesn't automatically mean contradiction — check the actual content\n"
            "5. Be precise about what conflicts; don't flag tangentially related topics as contradictions"
        ),
        output_schema=ConflictResolution,
        structured_outputs=True,
        markdown=False
    )
