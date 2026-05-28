"""Timeline class for timestamp synchronization across session components."""
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class TimelineEvent:
    """Represents a single event in the session timeline."""
    timestamp: datetime
    event_type: str
    data: Dict[str, Any] = field(default_factory=dict)
    
    def get_elapsed_seconds(self, session_start: datetime) -> float:
        """Get seconds elapsed since session start.
        
        Args:
            session_start: Session start time
            
        Returns:
            Seconds elapsed since session start
        """
        return (self.timestamp - session_start).total_seconds()


class Timeline:
    """Manages timeline synchronization for session events.
    
    Coordinates timestamps across audio recordings, screenshots, and transcripts
    to enable accurate correlation of session data.
    """
    
    EVENT_SESSION_START = 'session_start'
    EVENT_SESSION_END = 'session_end'
    EVENT_AUDIO_START = 'audio_start'
    EVENT_AUDIO_END = 'audio_end'
    EVENT_SCREENSHOT = 'screenshot'
    EVENT_TRANSCRIPT = 'transcript'
    EVENT_NOTE = 'note'
    
    def __init__(self, session_id: int, session_start: Optional[datetime] = None):
        """Initialize Timeline instance.
        
        Args:
            session_id: Database session ID
            session_start: Session start time (defaults to now)
        """
        self.session_id = session_id
        self.session_start = session_start or datetime.now()
        self.session_end: Optional[datetime] = None
        self.events: List[TimelineEvent] = []
        
        # Add session start event
        self._add_event(self.EVENT_SESSION_START, {'session_id': session_id})
    
    def set_session_start(self, start_time: datetime) -> None:
        """Set the session start time.
        
        Args:
            start_time: Session start datetime
        """
        self.session_start = start_time
        
        # Update session start event
        if self.events and self.events[0].event_type == self.EVENT_SESSION_START:
            self.events[0].timestamp = start_time
    
    def set_session_end(self, end_time: datetime) -> None:
        """Set the session end time.
        
        Args:
            end_time: Session end datetime
        """
        self.session_end = end_time
        self._add_event(self.EVENT_SESSION_END, {'session_id': self.session_id})
    
    def _add_event(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> TimelineEvent:
        """Add an event to the timeline.
        
        Args:
            event_type: Type of event
            data: Optional event data
            
        Returns:
            The created TimelineEvent
        """
        event = TimelineEvent(
            timestamp=datetime.now(),
            event_type=event_type,
            data=data or {}
        )
        self.events.append(event)
        return event
    
    def add_audio_start(self, label: str = 'recording') -> TimelineEvent:
        """Record audio recording start.
        
        Args:
            label: Recording label
            
        Returns:
            Created timeline event
        """
        return self._add_event(
            self.EVENT_AUDIO_START, 
            {'label': label, 'session_id': self.session_id}
        )
    
    def add_audio_end(self, filepath: str, label: str = 'recording') -> TimelineEvent:
        """Record audio recording end.
        
        Args:
            filepath: Path to saved audio file
            label: Recording label
            
        Returns:
            Created timeline event
        """
        return self._add_event(
            self.EVENT_AUDIO_END,
            {'filepath': filepath, 'label': label, 'session_id': self.session_id}
        )
    
    def add_screenshot(self, filepath: str, label: str = 'screenshot') -> TimelineEvent:
        """Record a screenshot event.
        
        Args:
            filepath: Path to screenshot file
            label: Screenshot label
            
        Returns:
            Created timeline event
        """
        return self._add_event(
            self.EVENT_SCREENSHOT,
            {'filepath': filepath, 'label': label, 'session_id': self.session_id}
        )
    
    def add_transcript(self, text: str, source: str, timestamp: datetime) -> TimelineEvent:
        """Record a transcript event.
        
        Args:
            text: Transcribed text
            source: Audio source ('microphone' or 'system')
            timestamp: When the audio was recorded
            
        Returns:
            Created timeline event
        """
        return self._add_event(
            self.EVENT_TRANSCRIPT,
            {'text': text, 'source': source, 'session_id': self.session_id}
        )
    
    def add_note(self, content: str) -> TimelineEvent:
        """Add a note to the timeline.
        
        Args:
            content: Note content
            
        Returns:
            Created timeline event
        """
        return self._add_event(
            self.EVENT_NOTE,
            {'content': content, 'session_id': self.session_id}
        )
    
    def get_events_by_type(self, event_type: str) -> List[TimelineEvent]:
        """Get all events of a specific type.
        
        Args:
            event_type: Type of events to retrieve
            
        Returns:
            List of matching events
        """
        return [e for e in self.events if e.event_type == event_type]
    
    def get_screenshots_near_audio(self, audio_timestamp: datetime, 
                                    tolerance_seconds: float = 5.0) -> List[TimelineEvent]:
        """Find screenshots taken near an audio segment.
        
        Args:
            audio_timestamp: When the audio segment was recorded
            tolerance_seconds: Time window to search
            
        Returns:
            List of screenshot events within tolerance
        """
        screenshots = self.get_events_by_type(self.EVENT_SCREENSHOT)
        results = []
        
        for screenshot in screenshots:
            diff = abs((screenshot.timestamp - audio_timestamp).total_seconds())
            if diff <= tolerance_seconds:
                results.append(screenshot)
        
        return results
    
    def get_transcripts_for_audio(self, audio_start: datetime, 
                                   audio_duration: float) -> List[TimelineEvent]:
        """Get transcripts that overlap with an audio segment.
        
        Args:
            audio_start: When audio recording started
            audio_duration: Duration of audio in seconds
            
        Returns:
            List of transcript events
        """
        transcripts = self.get_events_by_type(self.EVENT_TRANSCRIPT)
        audio_end = datetime.fromtimestamp(
            audio_start.timestamp() + audio_duration
        )
        results = []
        
        for transcript in transcripts:
            # Transcript timestamp should be within audio timeframe
            if audio_start <= transcript.timestamp <= audio_end:
                results.append(transcript)
        
        return results
    
    def get_session_duration(self) -> float:
        """Get total session duration in seconds.
        
        Returns:
            Duration in seconds
        """
        end = self.session_end or datetime.now()
        return (end - self.session_start).total_seconds()
    
    def get_timeline(self) -> List[Dict[str, Any]]:
        """Get the complete timeline as a list of dictionaries.
        
        Returns:
            List of event dictionaries sorted by timestamp
        """
        sorted_events = sorted(self.events, key=lambda e: e.timestamp)
        
        return [
            {
                'timestamp': e.timestamp.isoformat(),
                'elapsed_seconds': e.get_elapsed_seconds(self.session_start),
                'event_type': e.event_type,
                'data': e.data
            }
            for e in sorted_events
        ]
    
    def get_event_count(self) -> Dict[str, int]:
        """Get count of events by type.
        
        Returns:
            Dictionary with event type counts
        """
        counts = {}
        for event in self.events:
            counts[event.event_type] = counts.get(event.event_type, 0) + 1
        return counts
    
    def __repr__(self) -> str:
        return f'Timeline(session_id={self.session_id}, events={len(self.events)})'
