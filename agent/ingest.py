"""Ingest every supported document in the inbox. Unchanged files skipped by hash."""
import asyncio, glob, os, sys
import kb, store

INBOX = os.getenv("KB_INBOX", "/opt/aivoice/kb/inbox")
CONFIG = os.getenv("AGENT_CONFIG", "default")


async def main():
    force = "--force" in sys.argv
    # Driven by kb.SUPPORTED so this never falls behind the ingester. A glob
    # written here by hand is how a format gets added and the CLI keeps
    # ignoring it, with nothing to say why.
    files = sorted(f for ext in kb.SUPPORTED
                   for f in glob.glob(os.path.join(INBOX, f"*{ext}")))
    if not files:
        print(f"nothing to ingest in {INBOX} ({', '.join(kb.SUPPORTED)})")
        return

    print(f"{len(files)} file(s) in {INBOX}  (config={CONFIG})\n")
    for f in files:
        try:
            r = await kb.ingest_file(f, CONFIG, force=force)
        except Exception as e:
            r = {"file": os.path.basename(f), "status": "ERROR", "error": repr(e)}
        line = f"  {r['status']:10} {r['file']}"
        if r.get("chunks"):
            line += f"   {r.get('pages','?')}p  {r['chunks']} chunks  {r.get('tokens',0)} tok"
        if r.get("error"):
            line += f"\n             {r['error']}"
        print(line)

    rows = await (await store.pool()).fetch(
        "SELECT filename, page_count, chunk_count, enabled, updated_at "
        "FROM kb_documents WHERE config_name=$1 ORDER BY filename", CONFIG)
    print("\n--- knowledge base ---")
    for r in rows:
        print(f"  {'on ' if r['enabled'] else 'off'}  {r['filename']:40} "
              f"{r['page_count']}p  {r['chunk_count']} chunks  {r['updated_at']:%Y-%m-%d %H:%M}")
    await store.close()


asyncio.run(main())
