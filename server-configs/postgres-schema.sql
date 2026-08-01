--
-- PostgreSQL database dump
--

\restrict W2JBnTrPEsjGZ3rURprrQjWFxsfbWA8kBttVhUgcGwa8AZAazZeW8lVXRA0dMv4

-- Dumped from database version 16.14 (Debian 16.14-1.pgdg12+1)
-- Dumped by pg_dump version 16.14 (Debian 16.14-1.pgdg12+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: agent_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_config (
    id integer NOT NULL,
    name text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    language text DEFAULT 'hi-IN'::text NOT NULL,
    greeting text,
    instructions text NOT NULL,
    stt_provider text DEFAULT 'google'::text NOT NULL,
    stt_model text,
    llm_provider text DEFAULT 'google'::text NOT NULL,
    llm_model text DEFAULT 'gemini-flash-latest'::text NOT NULL,
    llm_temperature real DEFAULT 0.6 NOT NULL,
    tts_provider text DEFAULT 'google'::text NOT NULL,
    tts_model text,
    tts_voice text,
    allow_interrupt boolean DEFAULT true NOT NULL,
    max_turns integer DEFAULT 40 NOT NULL,
    max_duration_sec integer DEFAULT 600 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    kb_enabled boolean DEFAULT false NOT NULL,
    kb_top_k integer DEFAULT 3 NOT NULL,
    kb_min_score real DEFAULT 0.25 NOT NULL,
    kb_inline_max_tokens integer DEFAULT 6000 NOT NULL,
    kb_summary text,
    transfer_enabled boolean DEFAULT true NOT NULL,
    transfer_to text DEFAULT 'sip:800@10.130.9.243'::text NOT NULL,
    transfer_message text,
    max_prompt_tokens integer DEFAULT 150000 NOT NULL,
    limit_message text
);


--
-- Name: agent_config_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.agent_config_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: agent_config_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.agent_config_id_seq OWNED BY public.agent_config.id;


--
-- Name: calls; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.calls (
    id bigint NOT NULL,
    room_name text NOT NULL,
    sip_call_id text,
    caller text,
    callee text,
    config_name text,
    language text,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    ended_at timestamp with time zone,
    duration_ms integer,
    end_reason text,
    outcome text,
    recording_path text,
    transferred_to text,
    transfer_reason text,
    llm_prompt_tokens integer,
    llm_prompt_cached_tokens integer,
    llm_completion_tokens integer,
    tts_characters integer,
    tts_audio_seconds real,
    stt_audio_seconds real,
    turn_count integer,
    limit_hit text
);


--
-- Name: calls_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.calls_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: calls_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.calls_id_seq OWNED BY public.calls.id;


--
-- Name: kb_chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kb_chunks (
    id bigint NOT NULL,
    doc_id bigint NOT NULL,
    config_name text DEFAULT 'default'::text NOT NULL,
    seq integer NOT NULL,
    page integer,
    heading text,
    content text NOT NULL,
    n_tokens integer,
    embedding public.vector(1536),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: kb_chunks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.kb_chunks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: kb_chunks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.kb_chunks_id_seq OWNED BY public.kb_chunks.id;


--
-- Name: kb_documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kb_documents (
    id bigint NOT NULL,
    config_name text DEFAULT 'default'::text NOT NULL,
    filename text NOT NULL,
    title text,
    content_hash text NOT NULL,
    page_count integer,
    chunk_count integer,
    language text,
    enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: kb_documents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.kb_documents_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: kb_documents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.kb_documents_id_seq OWNED BY public.kb_documents.id;


--
-- Name: turns; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.turns (
    id bigint NOT NULL,
    call_id bigint,
    seq integer NOT NULL,
    role text NOT NULL,
    text text,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    eou_ms integer,
    stt_ms integer,
    llm_ttft_ms integer,
    tts_ttfb_ms integer,
    total_ms integer,
    interrupted boolean DEFAULT false NOT NULL,
    kb_chunk_ids bigint[],
    kb_scores real[]
);


--
-- Name: turns_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.turns_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: turns_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.turns_id_seq OWNED BY public.turns.id;


--
-- Name: agent_config id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_config ALTER COLUMN id SET DEFAULT nextval('public.agent_config_id_seq'::regclass);


--
-- Name: calls id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.calls ALTER COLUMN id SET DEFAULT nextval('public.calls_id_seq'::regclass);


--
-- Name: kb_chunks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kb_chunks ALTER COLUMN id SET DEFAULT nextval('public.kb_chunks_id_seq'::regclass);


--
-- Name: kb_documents id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kb_documents ALTER COLUMN id SET DEFAULT nextval('public.kb_documents_id_seq'::regclass);


--
-- Name: turns id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.turns ALTER COLUMN id SET DEFAULT nextval('public.turns_id_seq'::regclass);


--
-- Name: agent_config agent_config_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_config
    ADD CONSTRAINT agent_config_name_key UNIQUE (name);


--
-- Name: agent_config agent_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_config
    ADD CONSTRAINT agent_config_pkey PRIMARY KEY (id);


--
-- Name: calls calls_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.calls
    ADD CONSTRAINT calls_pkey PRIMARY KEY (id);


--
-- Name: kb_chunks kb_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kb_chunks
    ADD CONSTRAINT kb_chunks_pkey PRIMARY KEY (id);


--
-- Name: kb_documents kb_documents_config_name_filename_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kb_documents
    ADD CONSTRAINT kb_documents_config_name_filename_key UNIQUE (config_name, filename);


--
-- Name: kb_documents kb_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kb_documents
    ADD CONSTRAINT kb_documents_pkey PRIMARY KEY (id);


--
-- Name: turns turns_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.turns
    ADD CONSTRAINT turns_pkey PRIMARY KEY (id);


--
-- Name: calls_room_name_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX calls_room_name_idx ON public.calls USING btree (room_name);


--
-- Name: calls_started_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX calls_started_at_idx ON public.calls USING btree (started_at DESC);


--
-- Name: kb_chunks_config_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX kb_chunks_config_idx ON public.kb_chunks USING btree (config_name);


--
-- Name: kb_chunks_doc_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX kb_chunks_doc_idx ON public.kb_chunks USING btree (doc_id);


--
-- Name: kb_chunks_embedding_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX kb_chunks_embedding_idx ON public.kb_chunks USING hnsw (embedding public.vector_cosine_ops);


--
-- Name: kb_chunks_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX kb_chunks_trgm_idx ON public.kb_chunks USING gin (content public.gin_trgm_ops);


--
-- Name: turns_call_id_seq_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX turns_call_id_seq_idx ON public.turns USING btree (call_id, seq);


--
-- Name: kb_chunks kb_chunks_doc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kb_chunks
    ADD CONSTRAINT kb_chunks_doc_id_fkey FOREIGN KEY (doc_id) REFERENCES public.kb_documents(id) ON DELETE CASCADE;


--
-- Name: turns turns_call_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.turns
    ADD CONSTRAINT turns_call_id_fkey FOREIGN KEY (call_id) REFERENCES public.calls(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict W2JBnTrPEsjGZ3rURprrQjWFxsfbWA8kBttVhUgcGwa8AZAazZeW8lVXRA0dMv4

