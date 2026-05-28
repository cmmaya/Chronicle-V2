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
    
    # Full Template - comprehensive summary
    FULL = SummaryTemplate(
        template_type=TemplateType.FULL,
        system_prompt="You are a meeting assistant that creates comprehensive summaries. "
                      "Extract key points, action items, decisions, and notable moments. "
                      "Be thorough but organized. Use clear formatting.",
        user_template="Create a comprehensive summary of this meeting.\n\n"
                      "Transcript:\n{transcript}{context}\n\n"
                      "Include:\n"
                      "1. Overview (2-3 sentences)\n"
                      "2. Key Points (5-7 items)\n"
                      "3. Action Items (with assignees if mentioned)\n"
                      "4. Decisions Made\n"
                      "5. Next Steps (if any)\n\n"
                      "Be concise but capture all important information.",
        fields=["overview", "key_points", "action_items", "decisions", "next_steps"]
    )
    
    # General Transcript Template - single API call for all summary types
    GENERAL_TRANSCRIPT_SUMMARY = SummaryTemplate(
        template_type=TemplateType.GENERAL_TRANSCRIPT,
        system_prompt=(
            "You are a transcript analysis assistant. You summarize transcripts from audio recordings, "
            "which may come from meetings, classes, lectures, interviews, calls, tutorials, personal notes, "
            "or mixed microphone and system audio. "
            "Use only information explicitly present in the transcript. Do not add external context, "
            "assumptions, interpretations, recommendations, or invented details. "
            "Do not infer names, dates, responsibilities, decisions, intentions, causes, or next steps "
            "unless they are clearly stated in the transcript. "
            "If information is missing, unclear, ambiguous, or not specified, use 'Not specified' or "
            "'Unclear in transcript'. "
            "Summarize faithfully and concisely. Group related information when useful, but do not change "
            "the meaning of what was said. "
            "Do not add sections, commentary, advice, conclusions, or suggestions beyond the requested format."
        ),
        user_template=(
            "Analyze the following transcript.\n\n"
            "Transcript:\n{transcript}{context}\n\n"
            "Return the result using exactly the structure below. Do not add extra sections.\n\n"

            "1. Overview\n"
            "Write one concise paragraph summarizing the main topic described in the transcript and the "
            "relevant context explicitly mentioned. Maximum 100 words. Do not include information that is "
            "not present in the transcript.\n\n"

            "2. Key Points\n"
            "Provide 3-7 focused key points. Each key point must represent a distinct topic, section, "
            "explanation, argument, decision, statement, event, or relevant detail from the transcript. "
            "Group related ideas together and avoid repetition. Do not invent importance or implications "
            "that are not stated.\n\n"

            "Format:\n"
            "1. <key point>\n"
            "2. <key point>\n"
            "3. <key point>\n\n"

            "3. Action Items\n"
            "List only explicit tasks, assignments, follow-ups, or requested actions mentioned in the "
            "transcript. Do not infer tasks from general discussion. Do not create action items from vague "
            "ideas unless a concrete action was explicitly requested or assigned.\n\n"

            "Use this exact format for each action item:\n"
            "Title: <short task title>\n"
            "Description: <what needs to be done, under 50 words>\n"
            "Due date: <explicit date/deadline or 'Not specified'>\n"
            "Responsible: <explicit person/role or 'Not specified'>\n\n"

            "If no explicit action items are found, write exactly:\n"
            "No explicit action items were found in the transcript.\n\n"

            "4. Decisions\n"
            "List only explicit decisions, agreements, conclusions, or resolutions clearly stated in the "
            "transcript. Do not infer decisions from discussion, preferences, or unresolved ideas.\n\n"

            "Format:\n"
            "1. <decision>\n"
            "2. <decision>\n\n"

            "If no explicit decisions are found, write exactly:\n"
            "No explicit decisions were found in the transcript.\n\n"

            "5. Open Questions or Unclear Points\n"
            "List only explicit questions, unresolved issues, pending topics, ambiguities, or unclear parts "
            "present in the transcript. Do not add questions that were not raised or implied clearly by the "
            "speakers.\n\n"

            "Format:\n"
            "1. <open question or unclear point>\n"
            "2. <open question or unclear point>\n\n"

            "If none are found, write exactly:\n"
            "No explicit open questions or unclear points were found in the transcript."
        ),
        fields=[
            "overview",
            "key_points",
            "action_items",
            "decisions",
            "open_questions"
        ]
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
