"""Members, payment ledger, aliases, and background spreadsheet imports."""

import os
from typing import Annotated, Callable, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, Response, UploadFile, status

from core import members, misc
from models.common import Page
from models.enums import TaskKind
from models.members import LedgerEntry, Member, MemberInput
from models.misc import Alias, AliasInput, Task

from ..background import BackgroundRunner, ImportResult
from ..deps import Conn, Runner, upload_to_temp_file
from ..errors import PROBLEM_RESPONSES, problems

router = APIRouter(responses=PROBLEM_RESPONSES)


@router.get("/members", response_model=Page[Member], tags=["members"],
            summary="Search members by name or entity code")
def search_members(
    conn: Conn,
    search: Optional[str] = None,
    active_only: bool = True,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> Page[Member]:
    return members.search_members(conn, term=search, active_only=active_only, limit=limit)


@router.post("/members", responses=problems(409), response_model=Member, status_code=status.HTTP_201_CREATED, tags=["members"])
def create_member(conn: Conn, body: MemberInput) -> Member:
    return members.create_member(conn, body)


def _start_import(
    request: Request, response: Response, runner: BackgroundRunner, file: UploadFile,
    kind: TaskKind, work: Callable[[object, str], ImportResult],
) -> Task:
    path = upload_to_temp_file(request, file)

    def run(conn: object) -> ImportResult:
        try:
            return work(conn, path)
        finally:
            os.unlink(path)

    task = runner.submit_import(kind, run)
    response.headers["Location"] = f"/api/tasks/{task.id}"
    return task


@router.post("/member-imports", responses=problems(413), response_model=Task, status_code=status.HTTP_202_ACCEPTED, tags=["members"],
             summary="Load an Account List workbook in the background")
def import_members(
    request: Request, response: Response, runner: Runner,
    file: Annotated[UploadFile, File(description="Account List.xlsx")],
    replace_all: Annotated[bool, Form()] = False,
) -> Task:
    return _start_import(request, response, runner, file, "member_import",
                         lambda conn, path: members.import_account_list(conn, path, replace_all))


@router.get("/ledger-entries", response_model=Page[LedgerEntry], tags=["ledger"],
            summary="Most recent payment ledger entries")
def list_ledger_entries(conn: Conn, limit: Annotated[int, Query(ge=1, le=500)] = 30) -> Page[LedgerEntry]:
    return members.list_ledger_entries(conn, limit=limit)


@router.post("/ledger-imports", responses=problems(413), response_model=Task, status_code=status.HTTP_202_ACCEPTED, tags=["ledger"],
             summary="Load a Ledger workbook in the background")
def import_ledger(
    request: Request, response: Response, runner: Runner,
    file: Annotated[UploadFile, File(description="Ledger.xlsx")],
    sheet_name: Annotated[str, Form(min_length=1)] = "Ledger",
) -> Task:
    return _start_import(request, response, runner, file, "ledger_import",
                         lambda conn, path: members.import_ledger(conn, path, sheet_name))


@router.get("/tasks/{task_id}", responses=problems(404), response_model=Task, tags=["tasks"], summary="Status of a background import")
def get_task(runner: Runner, task_id: str) -> Task:
    task = runner.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found (tasks are not kept across restarts).")
    return task


@router.get("/aliases", response_model=list[Alias], tags=["aliases"])
def list_aliases(conn: Conn, limit: Annotated[int, Query(ge=1, le=1000)] = 200) -> list[Alias]:
    return misc.list_aliases(conn, limit=limit)


@router.post("/aliases", responses=problems(404, 409), response_model=Alias, status_code=status.HTTP_201_CREATED, tags=["aliases"])
def create_alias(conn: Conn, body: AliasInput) -> Alias:
    return misc.create_alias(conn, body)
