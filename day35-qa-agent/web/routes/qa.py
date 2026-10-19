"""Day 35: the QA tab - `core.services.qa.QaService`, over HTTP.

    GET    /api/qa                          config, simulators, the project's Xcode projects,
                                            the suite, the job running now, the last runs
    GET    /api/qa/apps?udid=               the apps installed on a simulator
    GET    /api/qa/schemes?container=       the schemes of one of the project's Xcode projects
    POST   /api/qa/build                    job: build a scheme for a simulator and install it
    POST   /api/qa/runs                     job: run a scenario on an app - {"job", "run"}
    POST   /api/qa/stop                     stop the job running now
    GET    /api/qa/runs/{id}                a run: steps, checks, verdict, diagnosis (live while it runs)
    GET    /api/qa/runs/{id}/files/{name}   a step's screenshot
    GET    /api/qa/runs/{id}/report         the run as Markdown
    POST   /api/qa/runs/{id}/test           a passed run -> a generated XCUITest in the suite
    POST   /api/qa/scenarios                save a scenario to the suite
    PUT    /api/qa/scenarios/{id}           edit one
    DELETE /api/qa/scenarios/{id}           delete one, with its test
    GET    /api/qa/scenarios/{id}/test      the generated test's Swift source
    POST   /api/qa/tests                    job: run the suite's generated XCUITests
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse, PlainTextResponse

from web.deps import Runtime
from web.schemas import QaBuildRequest, QaRunRequest, QaScenarioRequest, QaTestsRequest

router = APIRouter(prefix="/api/qa")


@router.get("")
def get_qa(rt: Runtime) -> dict:
    return rt.qa.report()


@router.get("/apps")
def get_apps(udid: str, rt: Runtime) -> dict:
    return {"apps": rt.qa.apps(udid)}


@router.get("/schemes")
def get_schemes(container: str, rt: Runtime) -> dict:
    return {"schemes": rt.qa.schemes(container)}


@router.post("/build")
def build(req: QaBuildRequest, rt: Runtime) -> dict:
    return rt.qa.build(req.udid, req.container, req.scheme)


@router.post("/runs")
def start_run(req: QaRunRequest, rt: Runtime) -> dict:
    return rt.qa.start_run(udid=req.udid, bundle_id=req.bundle_id, text=req.text, title=req.title,
                           scenario_id=req.scenario_id, model=req.model, diagnose=req.diagnose)


@router.post("/stop")
def stop(rt: Runtime) -> dict:
    return rt.qa.stop()


@router.get("/runs/{run_id}")
def get_run(run_id: str, rt: Runtime) -> dict:
    return rt.qa.run(run_id)


@router.get("/runs/{run_id}/files/{name}")
def get_run_file(run_id: str, name: str, rt: Runtime) -> FileResponse:
    return FileResponse(rt.qa.run_file(run_id, name), media_type="image/png")


@router.get("/runs/{run_id}/report")
def get_run_report(run_id: str, rt: Runtime) -> PlainTextResponse:
    return PlainTextResponse(rt.qa.run_report(run_id), media_type="text/markdown; charset=utf-8")


@router.post("/runs/{run_id}/test")
def make_test(run_id: str, rt: Runtime) -> dict:
    return rt.qa.make_test(run_id)


@router.post("/scenarios")
def save_scenario(req: QaScenarioRequest, rt: Runtime) -> dict:
    return rt.qa.save_scenario(req.title, req.text, req.bundle_id, req.app_name)


@router.put("/scenarios/{scenario_id}")
def update_scenario(scenario_id: str, req: QaScenarioRequest, rt: Runtime) -> dict:
    return rt.qa.update_scenario(scenario_id, req.title, req.text)


@router.delete("/scenarios/{scenario_id}")
def delete_scenario(scenario_id: str, rt: Runtime) -> dict:
    return rt.qa.delete_scenario(scenario_id)


@router.get("/scenarios/{scenario_id}/test")
def get_test_source(scenario_id: str, rt: Runtime) -> PlainTextResponse:
    return PlainTextResponse(rt.qa.test_source(scenario_id), media_type="text/plain; charset=utf-8")


@router.post("/tests")
def run_tests(req: QaTestsRequest, rt: Runtime) -> dict:
    return rt.qa.run_tests(req.udid, req.scenario_ids)
