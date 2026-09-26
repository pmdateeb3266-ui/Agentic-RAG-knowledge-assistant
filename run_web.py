import uvicorn

from backup_manager import apply_pending_restore

if __name__ == "__main__":
    restored = apply_pending_restore()
    if restored:
        print(
            "RESTORE_COMPLETE "
            f"documents={restored['database_counts']['documents']} "
            f"records={restored['database_counts']['records']}"
        )
    uvicorn.run("api:app", host="127.0.0.1", port=8000, reload=False)
