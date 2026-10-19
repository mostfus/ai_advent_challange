"""Day 35: the QA agent - a scenario in words, run on an iOS app in the Simulator by a model.

    config      `qa_agent.json`: the model, the simulator, the budget, the bridge's port
    simctl      the simulators: list, boot, show, the apps installed, install, screenshot
    xcode       an Xcode project's schemes, and a scheme built for the simulator
    bridge      the XCUITest bridge (`bridge/`): generated, built, started, stopped - and
                the generated scenario tests run with it
    crash       the crash report of an app that stopped running, read from its `.ips`
    suite       the QA suite (`qa_suite/`): saved scenarios and their generated XCUITests
    runner      one run: the app launched, the model driving it through
                `tools.simulator`, the verdict, the read-only code diagnosis of a failure
    diagnose    that diagnosis: a second agent with the project's files, read-only
    xcuitest    a passed run turned into an XCUITest - the same steps, found by label
    report      a run as Markdown, with its screenshots
    __main__    `python -m qa`: the suite run in CI, on a self-hosted Mac runner

The web app reaches all of it through `core.services.qa.QaService`.
"""
