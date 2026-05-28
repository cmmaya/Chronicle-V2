"""Audio chunk data structure for chunked audio capture."""

import json
from pathlib import Path
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional


@dataclass
class AudioChunk:
    """Represents a single audio chunk with metadata.
    
    Attributes:
        source: The origin of the audio ('mic' or 'system').
        chunk_id: A unique identifier for the chunk.
        timestamp_start: The absolute start time of the chunk.
        timestamp_end: The absolute end time of the chunk.
        file_path: The path to the corresponding audio file.
    """
    source: str
    chunk_id: str
    timestamp_start: str
    timestamp_end: str
    file_path: str
    
    def to_dict(self) -> dict:
        """Convert chunk metadata to dictionary.
        
        Returns:
            Dictionary representation of the chunk.
        """
        return asdict(self)
    
    def to_json(self) -> str:
        """Convert chunk metadata to JSON string.
        
        Returns:
            JSON string representation.
        """
        return json.dumps(self.to_dict(), indent=2)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'AudioChunk':
        """Create AudioChunk from dictionary.
        
        Args:
            data: Dictionary containing chunk data.
            
        Returns:
            AudioChunk instance.
        """
        return cls(**data)
    
    @classmethod
    def from_json(cls, json_str: str) -> 'AudioChunk':
        """Create AudioChunk from JSON string.
        
        Args:
            json_str: JSON string containing chunk data.
            
        Returns:
            AudioChunk instance.
        """
        return cls.from_dict(json.loads(json_str))
    
    @property
    def duration(self) -> float:
        """Calculate chunk duration in seconds.
        
        Returns:
            Duration in seconds.
        """
        start = datetime.fromisoformat(self.timestamp_start)
        end = datetime.fromisoformat(self.timestamp_end)
        return (end - start).total_seconds()
    
    @property
    def file_path_obj(self) -> Path:
        """Get file path as Path object.
        
        Returns:
            Path object for the audio file.
        """
        return Path(self.file_path)
    
    def save_metadata(self, base_path: Optional[Path] = None) -> None:
        """Save chunk metadata to JSON file.
        
        Args:
            base_path: Optional base path for the metadata file.
                       If not provided, saves next to the audio file.
        """
        if base_path is None:
            base_path = self.file_path_obj.parent
        
        metadata_path = base_path / f"{self.chunk_id}.json"
        with open(metadata_path, 'w') as f:
            f.write(self.to_json())
    
    @classmethod
    def load_metadata(cls, metadata_path: str) -> 'AudioChunk':
        """Load chunk metadata from JSON file.
        
        Args:
            metadata_path: Path to the metadata JSON file.
            
        Returns:
            AudioChunk instance.
        """
        with open(metadata_path, 'r') as f:
            return cls.from_json(f.read())


def generate_chunk_id(source: str, timestamp_start: datetime) -> str:
    """Generate a unique chunk ID.
    
    Args:
        source: The audio source ('mic' or 'system').
        timestamp_start: The start time of the chunk.
        
    Returns:
        Unique chunk ID string.
    """
    return f"{source}_{timestamp_start.strftime('%Y%m%d_%H%M%S')}_chunk"


def generate_filename(timestamp_start: datetime, timestamp_end: datetime) -> str:
    """Generate a chunk filename with timestamps.
    
    Args:
        timestamp_start: The start time of the chunk.
        timestamp_end: The end time of the chunk.
        
    Returns:
        Filename string with .wav extension.
    """
    start_str = timestamp_start.strftime('%Y%m%d_%H%M%S')
    end_str = timestamp_end.strftime('%Y%m%d_%H%M%S')
    return f"{start_str}_{end_str}.wav"