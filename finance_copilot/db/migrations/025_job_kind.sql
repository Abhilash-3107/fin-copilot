-- Background jobs now come in two kinds: 'annotate' (existing auto-annotation)
-- and 'reembed' (full re-embed after an embedding-provider switch). Both share
-- the annotation_jobs table and its single-job guard - a re-embed must block
-- annotation anyway because RAG retrieval is meaningless mid-rebuild.
ALTER TABLE annotation_jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'annotate';
