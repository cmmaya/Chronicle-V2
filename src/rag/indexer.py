"""RAG indexer for populating RAG tables with session content."""
import logging
import hashlib
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# Default chunk size for text chunking
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 100


def _chunk_text(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE,
                overlap: int = DEFAULT_CHUNK_OVERLAP) -> List[Dict[str, Any]]:
    """Split text into overlapping chunks.
    
    Args:
        text: The text to chunk
        chunk_size: Maximum size of each chunk in characters
        overlap: Number of characters to overlap between chunks
        
    Returns:
        List of chunk dictionaries with 'content' and 'chunk_index' keys
    """
    if not text or not text.strip():
        return []
    
    chunks = []
    text = text.strip()
    start = 0
    chunk_index = 0
    
    while start < len(text):
        end = start + chunk_size
        chunk_text = text[start:end]
        
        # Don't split in the middle of a word if possible
        if end < len(text) and ' ' in chunk_text[-(min(50, len(chunk_text))):]:
            # Find the last space to avoid cutting words
            last_space = chunk_text.rfind(' ')
            if last_space > chunk_size // 2:  # Only trim if not too close to chunk end
                chunk_text = chunk_text[:last_space]
                end = start + last_space
        
        chunks.append({
            'content': chunk_text.strip(),
            'chunk_index': chunk_index
        })
        
        chunk_index += 1
        start = end - overlap
        
        # Prevent infinite loop for very small texts
        if start <= chunks[-1]['chunk_index'] * chunk_size:
            break
    
    return chunks


def _compute_content_hash(content: str) -> str:
    """Compute a hash of the content for deduplication.
    
    Args:
        content: The content to hash
        
    Returns:
        SHA256 hash of the content as a hex string
    """
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def index_session_content(db, session_id: int) -> bool:
    """Index session content (transcripts and summaries) into RAG tables.
    
    This function fetches all transcripts and summaries for a session,
    creates RAG documents for each, chunks the content, and stores the
    chunks in the database. Finally, it rebuilds the FTS index.
    
    Args:
        db: Database instance with RAG methods
        session_id: ID of the session to index
        
    Returns:
        True if indexing succeeded, False otherwise
    """
    try:
        logger.info(f"Starting RAG indexing for session {session_id}")
        
        # Fetch all transcripts for the session
        transcripts = db.get_transcripts(session_id)
        
        if transcripts:
            # Combine all transcript text
            full_transcript = ' '.join(
                t.get('text', '') for t in transcripts if t.get('text')
            )
            
            if full_transcript.strip():
                # Get timestamp from first transcript (or use current time)
                timestamp = int(transcripts[0].get('timestamp', 0)) if transcripts else 0
                if timestamp == 0:
                    from datetime import datetime
                    timestamp = int(datetime.now().timestamp())
                
                # Compute content hash for deduplication
                content_hash = _compute_content_hash(full_transcript)
                
                # Create metadata
                metadata = {
                    'source': 'transcript',
                    'transcript_count': len(transcripts)
                }
                import json
                metadata_json = json.dumps(metadata)
                
                # Upsert RAG document for transcript (use source_id = session_id for transcript)
                doc_id = db.upsert_rag_document(
                    source_type='transcript',
                    source_id=session_id,  # Use session_id as source_id for transcript
                    session_id=session_id,
                    timestamp=timestamp,
                    title=f'Session {session_id} Transcript',
                    content_hash=content_hash,
                    metadata_json=metadata_json
                )
                
                # Chunk the transcript and store
                chunks = _chunk_text(full_transcript)
                if chunks:
                    db.replace_rag_chunks(doc_id, chunks)
                    logger.info(f"Indexed {len(chunks)} transcript chunks for session {session_id}")
        
        # Fetch all summaries for the session
        summaries = db.get_summaries(session_id)
        
        for summary in summaries:
            summary_content = summary.get('content', '')
            if not summary_content:
                continue
            
            # Get summary timestamp
            summary_timestamp = summary.get('created_at', 0)
            if summary_timestamp == 0:
                from datetime import datetime
                summary_timestamp = int(datetime.now().timestamp())
            
            # Compute content hash
            content_hash = _compute_content_hash(summary_content)
            
            # Create metadata
            summary_type = summary.get('summary_type', 'unknown')
            metadata = {
                'source': 'summary',
                'summary_type': summary_type,
                'model_used': summary.get('model_used', 'unknown')
            }
            import json
            metadata_json = json.dumps(metadata)
            
            # Upsert RAG document for summary (use summary id as source_id)
            summary_id = summary.get('id')
            doc_id = db.upsert_rag_document(
                source_type='summary',
                source_id=summary_id,
                session_id=session_id,
                timestamp=summary_timestamp,
                title=f'Session {session_id} - {summary_type}',
                content_hash=content_hash,
                metadata_json=metadata_json
            )
            
            # Chunk the summary and store
            chunks = _chunk_text(summary_content)
            if chunks:
                db.replace_rag_chunks(doc_id, chunks)
                logger.info(f"Indexed {len(chunks)} summary chunks for session {session_id} ({summary_type})")
        
        # Rebuild FTS index to make content searchable
        db.rebuild_rag_fts()
        logger.info(f"RAG indexing completed for session {session_id}")
        
        return True
        
    except Exception as e:
        logger.error(f"RAG indexing failed for session {session_id}: {str(e)}")
        return False
