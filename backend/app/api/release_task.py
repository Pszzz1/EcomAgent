from typing import NoReturn

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from backend.app.schemas import (
    APIErrorResponse,
    ReleaseTaskInput,
    ReleaseTaskResult,
    ReleaseTaskSummary,
    ReleaseTaskTurnInput,
)
from backend.app.infrastructure.persistence import TaskLeaseUnavailable
from backend.app.services import ReleaseTaskAgentService, ReleaseTaskConflictError

router = APIRouter(prefix="/release-tasks", tags=["release-tasks"])


ERROR_RESPONSES = {
    404: {"model": APIErrorResponse},
    409: {"model": APIErrorResponse},
}


def get_service(request: Request) -> ReleaseTaskAgentService:
    return request.app.state.release_task_service


@router.post("", response_model=ReleaseTaskResult, responses=ERROR_RESPONSES)
async def create_release_task(
    task_brief: str = Form(...),
    product_image: UploadFile = File(...),
    service: ReleaseTaskAgentService = Depends(get_service),
) -> ReleaseTaskResult:
    try:
        result = service.create_release_task(
            ReleaseTaskInput(task_brief=task_brief),
            image_filename=product_image.filename or "product-image",
            image_content_type=product_image.content_type or "",
            image_content=await product_image.read(15 * 1024 * 1024 + 1),
        )
    except ValueError as exc:
        _raise_api_error(422, "invalid_product_image", str(exc), exc)
    except (ReleaseTaskConflictError, TaskLeaseUnavailable) as exc:
        _raise_api_error(409, "task_conflict", str(exc), exc)
    return result


@router.post("/{task_id}/continue", response_model=ReleaseTaskResult, responses=ERROR_RESPONSES)
def continue_release_task(
    task_id: str,
    user_input: ReleaseTaskTurnInput,
    service: ReleaseTaskAgentService = Depends(get_service),
) -> ReleaseTaskResult:
    try:
        result = service.continue_release_task(task_id, user_input)
    except (ReleaseTaskConflictError, TaskLeaseUnavailable) as exc:
        _raise_api_error(409, "task_conflict", str(exc), exc)
    except KeyError as exc:
        _raise_api_error(404, "task_not_found", f"Release task not found: {task_id}", exc)
    except ValueError as exc:
        _raise_api_error(422, "invalid_confirmation_resolution", str(exc), exc)
    return result


@router.post("/{task_id}/product-image", response_model=ReleaseTaskResult, responses=ERROR_RESPONSES)
async def replace_product_image(
    task_id: str,
    product_image: UploadFile = File(...),
    expected_state_version: int | None = Form(None),
    service: ReleaseTaskAgentService = Depends(get_service),
) -> ReleaseTaskResult:
    try:
        result = service.replace_product_image(
            task_id,
            image_filename=product_image.filename or "product-image",
            image_content_type=product_image.content_type or "",
            image_content=await product_image.read(15 * 1024 * 1024 + 1),
            expected_state_version=expected_state_version,
        )
    except ValueError as exc:
        _raise_api_error(422, "invalid_product_image", str(exc), exc)
    except (ReleaseTaskConflictError, TaskLeaseUnavailable) as exc:
        _raise_api_error(409, "task_conflict", str(exc), exc)
    except KeyError as exc:
        _raise_api_error(404, "task_not_found", f"Release task not found: {task_id}", exc)
    return result


@router.get("", response_model=list[ReleaseTaskSummary])
def list_release_tasks(
    service: ReleaseTaskAgentService = Depends(get_service),
) -> list[ReleaseTaskSummary]:
    return service.list_tasks()


@router.get("/{task_id}", response_model=ReleaseTaskResult, responses=ERROR_RESPONSES)
def get_release_task(
    task_id: str,
    service: ReleaseTaskAgentService = Depends(get_service),
) -> ReleaseTaskResult:
    result = service.get_task(task_id)
    if result is None:
        _raise_api_error(404, "task_not_found", f"Release task not found: {task_id}")
    return result


@router.get("/{task_id}/assets/{asset_id}", responses=ERROR_RESPONSES)
def get_release_asset(
    task_id: str,
    asset_id: str,
    service: ReleaseTaskAgentService = Depends(get_service),
) -> FileResponse:
    try:
        asset, path = service.get_image_asset(task_id, asset_id)
    except KeyError as exc:
        _raise_api_error(404, "asset_not_found", "Release task image was not found.", exc)
    return FileResponse(path, media_type=asset.mime_type)


@router.delete("/{task_id}", status_code=204, responses=ERROR_RESPONSES)
def delete_release_task(
    task_id: str,
    service: ReleaseTaskAgentService = Depends(get_service),
) -> None:
    try:
        service.delete_release_task(task_id)
    except TaskLeaseUnavailable as exc:
        _raise_api_error(409, "task_conflict", str(exc), exc)
    except KeyError as exc:
        _raise_api_error(404, "task_not_found", f"Release task not found: {task_id}", exc)


def _raise_api_error(
    status_code: int,
    code: str,
    message: str,
    cause: Exception | None = None,
) -> NoReturn:
    error = HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )
    if cause is not None:
        raise error from cause
    raise error
