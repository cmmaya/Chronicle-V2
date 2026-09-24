"""Summary templates for structuring meeting summaries."""
from enum import Enum
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field


class TemplateType(Enum):
    """Types of summary templates."""
    KEY_POINTS = "key_points"
    ACTION_ITEMS = "action_items"
    DECISIONS = "decisions"
    FULL = "full"
    GENERAL_TRANSCRIPT = "general_transcript"


@dataclass
class SummaryTemplate:
    """Template for generating structured summaries.
    
    Attributes:
        template_type: Type of template
        system_prompt: System prompt for the AI model
        user_template: Template for formatting user messages
        fields: List of field names to extract
    """
    template_type: TemplateType
    system_prompt: str
    user_template: str
    fields: List[str] = field(default_factory=list)
    
    def format_user_message(self, transcript: str, context: Optional[Dict[str, Any]] = None) -> str:
        """Format user message with transcript and context.
        
        Args:
            transcript: Raw transcript text
            context: Optional context dictionary
            
        Returns:
            Formatted user message
        """
        context_str = ""
        if context:
            context_items = [f"{k}: {v}" for k, v in context.items()]
            context_str = "\n".join(context_items)
            context_str = f"\n\nMeeting Context:\n{context_str}"
        
        return self.user_template.format(transcript=transcript, context=context_str)


# Shared by FULL (the template the app actually uses via generate_and_store)
# and GENERAL_TRANSCRIPT, so the two never drift apart. Section 3 reports due
# dates only - undated tasks are intentionally dropped.
_TRANSCRIPT_SYSTEM_PROMPT = (
    "You are a transcript analysis assistant. You summarize transcripts from audio recordings, "
    "which may come from meetings, classes, lectures, interviews, calls, tutorials, personal notes, "
    "or mixed microphone and system audio. "
    "Use only information explicitly present in the transcript. Do not add external context, "
    "assumptions, interpretations, recommendations, or invented details. "
    "Do not infer names, dates, responsibilities, decisions, intentions, causes, or next steps "
    "unless they are clearly stated in the transcript. "
    "If information is missing, unclear, or ambiguous, use 'Not specified' or "
    "'Unclear in transcript'.\n\n"
    "Due dates are the most important information in any transcript. Read the entire transcript "
    "for them, including passing remarks, corrections, and asides near the end of the recording. "
    "Missing a stated deadline is a worse error than reporting one whose wording is unclear: if a "
    "deadline was mentioned but is hard to make out, report it and mark it as unclear rather than "
    "leaving it out.\n\n"
    "Summarize faithfully and concisely. Do not add sections, commentary, advice, or conclusions "
    "beyond the requested format."
)

_TRANSCRIPT_USER_TEMPLATE = (
    "Analyze the following transcript.\n\n"
    "Transcript:\n{transcript}{context}\n\n"
    "Return the result using exactly the structure below. Do not add extra sections.\n\n"

    "1. Overview\n"
    "Write one concise paragraph summarizing the main topic and the relevant context explicitly "
    "mentioned. Maximum 100 words. If the transcript contains any due dates, end the paragraph "
    "with one sentence stating how many there are and which one is nearest.\n\n"

    "2. Key Points\n"
    "Provide 3-7 focused key points. Each must represent a distinct topic, explanation, argument, "
    "decision, statement, event, or relevant detail from the transcript. Group related ideas and "
    "avoid repetition. Do not invent importance or implications that are not stated.\n\n"

    "Format:\n"
    "1. <key point>\n"
    "2. <key point>\n\n"

    "3. Due Dates\n"
    "Report every deadline or due date mentioned in the transcript: anything that must be done, "
    "delivered, submitted, paid, answered, or attended by a specific date or time. Include an "
    "entry only when a date or time reference is explicitly tied to something. Do not list tasks, "
    "follow-ups, or intentions that have no date attached. Never invent, estimate, or assume a "
    "date.\n\n"

    "Rules:\n"
    "- Write the date as it was spoken (\"next Friday\", \"end of the month\", \"the 15th\"). If "
    "the recording date appears in the Meeting Context, add the resolved calendar date in "
    "parentheses, e.g. \"next Friday (2026-09-18)\". If there is no recording date, do not "
    "resolve relative dates.\n"
    "- Include any stated time, time zone, or condition (\"before 5 pm\", \"before class "
    "starts\").\n"
    "- If a date was changed or postponed, report the final date and note the earlier one "
    "(\"moved from March 3\").\n"
    "- If speakers give conflicting dates, or the date is garbled or ambiguous, still report the "
    "entry and write: Unclear in transcript: <what was said>.\n"
    "- Calendar date is for the app, not the reader. Resolve it only from the Recording date in "
    "the Meeting Context. Write one of: \"YYYY-MM-DD\" (all-day), \"YYYY-MM-DD HH:MM\" (a "
    "stated time), \"YYYY-MM-DD HH:MM-HH:MM\" (a stated start and end time), or \"none\". Use a "
    "24-hour clock in the local time of the recording, with no time zone. Write a time only when "
    "one was stated; never write one for \"end of day\" or \"morning\". Write \"none\" when "
    "there is no recording date, or when the date is recurring, conditional, has two or more "
    "candidates, or is unclear.\n"
    "- List each distinct deadline once, even if it is repeated.\n"
    "- Order entries chronologically when the order is clear; otherwise keep the order in which "
    "they were mentioned.\n\n"

    "Use this exact format for each entry:\n"
    "Title: <what is due, 3-8 words>\n"
    "Due date: <date/time as stated, plus resolved date if available>\n"
    "Calendar date: <YYYY-MM-DD, YYYY-MM-DD HH:MM, YYYY-MM-DD HH:MM-HH:MM, or none>\n"
    "Description: <what must be done or delivered, and by whom if stated; under 40 words>\n\n"

    "If no due dates are mentioned, write exactly:\n"
    "No due dates were mentioned in the transcript.\n\n"

    "4. Decisions\n"
    "List only explicit decisions, agreements, conclusions, or resolutions clearly stated in the "
    "transcript. Do not infer decisions from discussion, preferences, or unresolved ideas.\n\n"

    "Format:\n"
    "1. <decision>\n\n"

    "If none are found, write exactly:\n"
    "No explicit decisions were found in the transcript.\n\n"

    "5. Open Questions or Unclear Points\n"
    "List only explicit questions, unresolved issues, pending topics, or unclear parts present in "
    "the transcript. Do not add questions the speakers did not raise.\n\n"

    "Format:\n"
    "1. <open question or unclear point>\n\n"

    "If none are found, write exactly:\n"
    "No explicit open questions or unclear points were found in the transcript."
)

_TRANSCRIPT_FIELDS = ["overview", "key_points", "due_dates", "decisions", "open_questions"]


class TemplateRegistry:
    """Registry of available summary templates."""
    
    # Key Points Template - extracts main topics and highlights
    KEY_POINTS = SummaryTemplate(
        template_type=TemplateType.KEY_POINTS,
        system_prompt="You are a meeting assistant that extracts key points from transcripts. "
                      "Focus on main topics, important decisions, and notable statements. "
                      "Be concise and actionable.",
        user_template="Extract the key points from this meeting transcript.\n\n"
                      "Transcript:\n{transcript}{context}\n\n"
                      "Provide 3-7 key points as a numbered list. Each point should be brief "
                      "but capture the essential information.",
        fields=["key_points"]
    )
    
    # Action Items Template - extracts tasks and follow-ups
    ACTION_ITEMS = SummaryTemplate(
        template_type=TemplateType.ACTION_ITEMS,
        system_prompt="You are a meeting assistant that identifies action items from transcripts. "
                      "Look for tasks, assignments, deadlines, and follow-up items. "
                      "Be specific about who is responsible and when tasks are due.",
        user_template="Identify all action items from this meeting transcript.\n\n"
                      "Transcript:\n{transcript}{context}\n\n"
                      "For each action item, specify:\n"
                      "- What needs to be done\n"
                      "- Who is responsible (if mentioned)\n"
                      "- Deadline (if mentioned)\n\n"
                      "Format as a numbered list. If no action items are found, state that clearly.",
        fields=["action_items", "assignees", "deadlines"]
    )
    
    # Decisions Template - extracts decisions made
    DECISIONS = SummaryTemplate(
        template_type=TemplateType.DECISIONS,
        system_prompt="You are a meeting assistant that identifies decisions made in meetings. "
                      "Look for conclusions, agreements, and formal decisions. "
                      "Be clear and specific about what was decided.",
        user_template="Identify all decisions made in this meeting transcript.\n\n"
                      "Transcript:\n{transcript}{context}\n\n"
                      "For each decision, provide:\n"
                      "- The decision made\n"
                      "- Who was involved in the decision\n"
                      "- Any relevant context\n\n"
                      "Format as a numbered list. If no explicit decisions are found, "
                      "note that only implicit conclusions were reached.",
        fields=["decisions", "stakeholders"]
    )
    
    # Full Template - the one generate_and_store(summary_type="full") uses, so
    # this is what every summary in the app runs on.
    FULL = SummaryTemplate(
        template_type=TemplateType.FULL,
        system_prompt=_TRANSCRIPT_SYSTEM_PROMPT,
        user_template=_TRANSCRIPT_USER_TEMPLATE,
        fields=_TRANSCRIPT_FIELDS,
    )

    # General Transcript Template - single API call for all summary types
    GENERAL_TRANSCRIPT_SUMMARY = SummaryTemplate(
        template_type=TemplateType.GENERAL_TRANSCRIPT,
        system_prompt=_TRANSCRIPT_SYSTEM_PROMPT,
        user_template=_TRANSCRIPT_USER_TEMPLATE,
        fields=_TRANSCRIPT_FIELDS,
    )
    
    @classmethod
    def get_template(cls, template_type: TemplateType) -> SummaryTemplate:
        """Get template by type.
        
        Args:
            template_type: Type of template to retrieve
            
        Returns:
            SummaryTemplate instance
            
        Raises:
            ValueError: If template type is not recognized
        """
        templates = {
            TemplateType.KEY_POINTS: cls.KEY_POINTS,
            TemplateType.ACTION_ITEMS: cls.ACTION_ITEMS,
            TemplateType.DECISIONS: cls.DECISIONS,
            TemplateType.FULL: cls.FULL,
            TemplateType.GENERAL_TRANSCRIPT: cls.GENERAL_TRANSCRIPT_SUMMARY,
        }
        
        if template_type not in templates:
            raise ValueError(f"Unknown template type: {template_type}")
        
        return templates[template_type]
    
    @classmethod
    def get_all_templates(cls) -> Dict[TemplateType, SummaryTemplate]:
        """Get all available templates.
        
        Returns:
            Dictionary of all templates by type
        """
        return {
            TemplateType.KEY_POINTS: cls.KEY_POINTS,
            TemplateType.ACTION_ITEMS: cls.ACTION_ITEMS,
            TemplateType.DECISIONS: cls.DECISIONS,
            TemplateType.FULL: cls.FULL,
            TemplateType.GENERAL_TRANSCRIPT: cls.GENERAL_TRANSCRIPT_SUMMARY,
        }
    
    @classmethod
    def list_template_types(cls) -> List[TemplateType]:
        """List all available template types.
        
        Returns:
            List of template types
        """
        return list(TemplateType)
