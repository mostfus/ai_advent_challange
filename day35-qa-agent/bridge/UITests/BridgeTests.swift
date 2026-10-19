// The bridge itself: a UI test that does not finish. `testServe` starts
// `BridgeServer` and runs every command it receives on the main thread - the
// only thread XCUITest may be driven from - until it is told to stop.
//
// The agent never sees XCUITest. It asks for the screen (`/tree`, the app's
// accessibility hierarchy as JSON), and acts by coordinates (`/tap`, `/swipe`)
// and by typing into whatever has focus (`/type`); `qa/screen.py` turns the
// tree into the text the model reads and an element ref back into a point.
//
//   GET  /health                                  is the bridge up
//   POST /launch     {bundle_id, fresh}           launch (fresh: terminate first) or bring forward
//   POST /tree       {bundle_id}                  the app's hierarchy, plus a system alert if one is up
//   POST /tap        {bundle_id, x, y, duration}  tap (or long-press) a point, in screen points
//   POST /type       {bundle_id, text}            type into the focused field; "\n" is Return
//   POST /swipe      {bundle_id, from, to, duration}  press and drag between two points
//   POST /state      {bundle_id}                  running in the foreground, background, or not
//   POST /terminate  {bundle_id}
//   POST /home                                    the Home button
//   POST /shutdown                                stop serving; the test ends
//
// A failed XCUITest call (no keyboard focus, an app that is not running)
// records an issue instead of throwing. `record(_:)` catches it while serving,
// so the command answers with the reason and the bridge keeps running.

import Foundation
import XCTest

final class BridgeTests: XCTestCase {
    private let inbox = Inbox()
    private var serving = false
    private var stopped = false
    private var issues: [String] = []

    override func record(_ issue: XCTIssue) {
        if serving {
            issues.append(Self.short(issue.compactDescription))
            return
        }
        super.record(issue)
    }

    /// The reason, without the element dump XCUITest appends to it.
    private static func short(_ text: String) -> String {
        var reason = text
        for marker in ["Event dispatch snapshot", "Element debug description", "\n"] {
            if let range = reason.range(of: marker) {
                reason = String(reason[..<range.lowerBound])
            }
        }
        reason = reason.trimmingCharacters(in: .whitespacesAndNewlines)
        return reason.count > 300 ? String(reason.prefix(300)) + "..." : reason
    }

    func testServe() throws {
        // Only when qa/bridge.py asks for it - a plain `xcodebuild test` of the
        // generated scenario tests skips the bridge instead of hanging on it.
        guard let raw = ProcessInfo.processInfo.environment["QA_BRIDGE_PORT"], let port = UInt16(raw) else {
            throw XCTSkip("QA_BRIDGE_PORT is not set: the bridge runs only when qa/bridge.py starts it")
        }
        continueAfterFailure = true
        let server = try BridgeServer(port: port) { [inbox] request in
            inbox.submit(request)
        }
        server.start()
        serving = true
        defer {
            serving = false
            server.stop()
        }
        NSLog("QA bridge: serving on 127.0.0.1:%d", Int(port))
        while !stopped {
            if let job = inbox.next(timeout: 0.05) {
                job.finish(run(job.request))
            } else {
                RunLoop.current.run(mode: .default, before: Date(timeIntervalSinceNow: 0.01))
            }
        }
    }

    // MARK: - Commands

    private func run(_ request: BridgeRequest) -> BridgeResponse {
        issues.removeAll()
        do {
            var answer = try dispatch(request)
            if !issues.isEmpty {
                return BridgeResponse(status: 200, json: ["ok": false, "error": issues.joined(separator: "; ")])
            }
            answer["ok"] = true
            return BridgeResponse(status: 200, json: answer)
        } catch {
            let reason = issues.isEmpty ? "\(error)" : issues.joined(separator: "; ")
            return BridgeResponse(status: 200, json: ["ok": false, "error": reason])
        }
    }

    private func dispatch(_ request: BridgeRequest) throws -> [String: Any] {
        let body = request.body
        switch (request.method, request.path) {
        case ("GET", "/health"):
            return ["bridge": "qa", "version": 1]
        case ("POST", "/launch"):
            let app = try application(body)
            if (body["fresh"] as? Bool) ?? true {
                if app.state != .notRunning { app.terminate() }
                app.launch()
            } else {
                app.activate()
            }
            return ["state": stateName(app.state)]
        case ("POST", "/tree"):
            let app = try application(body)
            var answer: [String: Any] = ["state": stateName(app.state)]
            if app.state == .runningForeground {
                answer["tree"] = node(try app.snapshot())
            }
            if let alert = systemAlert() {
                answer["system_alert"] = alert
            }
            return answer
        case ("POST", "/tap"):
            let point = try coordinate(body, "x", "y")
            let duration = (body["duration"] as? Double) ?? 0
            if duration > 0 {
                point.press(forDuration: duration)
            } else {
                point.tap()
            }
            return [:]
        case ("POST", "/type"):
            let app = try application(body)
            guard let text = body["text"] as? String, !text.isEmpty else { throw BridgeError("text is required") }
            app.typeText(text)
            return [:]
        case ("POST", "/swipe"):
            guard let from = body["from"] as? [Double], let to = body["to"] as? [Double],
                  from.count == 2, to.count == 2 else {
                throw BridgeError("from and to are [x, y]")
            }
            let start = try coordinate(["x": from[0], "y": from[1]], "x", "y")
            let end = try coordinate(["x": to[0], "y": to[1]], "x", "y")
            start.press(forDuration: (body["duration"] as? Double) ?? 0.05, thenDragTo: end)
            return [:]
        case ("POST", "/state"):
            return ["state": stateName(try application(body).state)]
        case ("POST", "/terminate"):
            let app = try application(body)
            if app.state != .notRunning { app.terminate() }
            return ["state": stateName(app.state)]
        case ("POST", "/home"):
            XCUIDevice.shared.press(.home)
            return [:]
        case ("POST", "/shutdown"):
            stopped = true
            return [:]
        default:
            throw BridgeError("no command \(request.method) \(request.path)")
        }
    }

    private func application(_ body: [String: Any]) throws -> XCUIApplication {
        guard let bundleID = body["bundle_id"] as? String, !bundleID.isEmpty else {
            throw BridgeError("bundle_id is required")
        }
        return XCUIApplication(bundleIdentifier: bundleID)
    }

    /// A point on the screen, in points from its top-left corner. Springboard
    /// is always running, so a tap lands wherever the point is - on the app,
    /// or on a system alert over it.
    private func coordinate(_ body: [String: Any], _ xKey: String, _ yKey: String) throws -> XCUICoordinate {
        guard let x = number(body[xKey]), let y = number(body[yKey]) else {
            throw BridgeError("\(xKey) and \(yKey) are numbers")
        }
        let screen = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        return screen.coordinate(withNormalizedOffset: CGVector(dx: 0, dy: 0)).withOffset(CGVector(dx: x, dy: y))
    }

    private func number(_ value: Any?) -> Double? {
        if let value = value as? Double { return value }
        if let value = value as? Int { return Double(value) }
        if let value = value as? NSNumber { return value.doubleValue }
        return nil
    }

    /// A permission prompt or any other alert Springboard shows over the app.
    private func systemAlert() -> [String: Any]? {
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        let alert = springboard.alerts.firstMatch
        guard alert.exists, let snapshot = try? alert.snapshot() else { return nil }
        return node(snapshot)
    }

    // MARK: - The hierarchy as JSON

    private func node(_ snapshot: XCUIElementSnapshot) -> [String: Any] {
        let frame = snapshot.frame
        var answer: [String: Any] = [
            "type": typeName(snapshot.elementType),
            "frame": [round(frame.origin.x), round(frame.origin.y), round(frame.size.width), round(frame.size.height)],
        ]
        if !snapshot.identifier.isEmpty { answer["id"] = snapshot.identifier }
        if !snapshot.label.isEmpty { answer["label"] = snapshot.label }
        if !snapshot.title.isEmpty { answer["title"] = snapshot.title }
        if let value = snapshot.value {
            let text = "\(value)"
            if !text.isEmpty { answer["value"] = text }
        }
        if let placeholder = snapshot.placeholderValue, !placeholder.isEmpty { answer["placeholder"] = placeholder }
        if !snapshot.isEnabled { answer["enabled"] = false }
        if snapshot.isSelected { answer["selected"] = true }
        // A keyboard is reported, not listed key by key.
        if snapshot.elementType != .keyboard {
            let children = snapshot.children.map { node($0) }
            if !children.isEmpty { answer["children"] = children }
        }
        return answer
    }

    private func round(_ value: CGFloat) -> Double {
        (Double(value) * 10).rounded() / 10
    }

    private func stateName(_ state: XCUIApplication.State) -> String {
        switch state {
        case .notRunning: return "not_running"
        case .runningBackgroundSuspended: return "background_suspended"
        case .runningBackground: return "background"
        case .runningForeground: return "foreground"
        default: return "unknown"
        }
    }

    private func typeName(_ type: XCUIElement.ElementType) -> String {
        switch type {
        case .application: return "Application"
        case .window: return "Window"
        case .sheet: return "Sheet"
        case .alert: return "Alert"
        case .dialog: return "Dialog"
        case .button: return "Button"
        case .radioButton: return "RadioButton"
        case .checkBox: return "CheckBox"
        case .popUpButton: return "PopUpButton"
        case .menuButton: return "MenuButton"
        case .toolbarButton: return "ToolbarButton"
        case .popover: return "Popover"
        case .keyboard: return "Keyboard"
        case .key: return "Key"
        case .navigationBar: return "NavigationBar"
        case .tabBar: return "TabBar"
        case .tabGroup: return "TabGroup"
        case .toolbar: return "Toolbar"
        case .statusBar: return "StatusBar"
        case .table: return "Table"
        case .tableRow: return "TableRow"
        case .outline: return "Outline"
        case .outlineRow: return "OutlineRow"
        case .collectionView: return "CollectionView"
        case .slider: return "Slider"
        case .pageIndicator: return "PageIndicator"
        case .progressIndicator: return "ProgressIndicator"
        case .activityIndicator: return "ActivityIndicator"
        case .segmentedControl: return "SegmentedControl"
        case .picker: return "Picker"
        case .pickerWheel: return "PickerWheel"
        case .switch: return "Switch"
        case .toggle: return "Toggle"
        case .link: return "Link"
        case .image: return "Image"
        case .icon: return "Icon"
        case .searchField: return "SearchField"
        case .scrollView: return "ScrollView"
        case .scrollBar: return "ScrollBar"
        case .staticText: return "StaticText"
        case .textField: return "TextField"
        case .secureTextField: return "SecureTextField"
        case .datePicker: return "DatePicker"
        case .textView: return "TextView"
        case .menu: return "Menu"
        case .menuItem: return "MenuItem"
        case .map: return "Map"
        case .webView: return "WebView"
        case .cell: return "Cell"
        case .stepper: return "Stepper"
        case .tab: return "Tab"
        case .group: return "Group"
        case .other: return "Other"
        default: return "Type\(type.rawValue)"
        }
    }
}

/// Requests waiting for the main thread, each with the network thread that
/// sent it blocked until its answer is in.
private final class Inbox {
    final class Job {
        let request: BridgeRequest
        private let done = DispatchSemaphore(value: 0)
        private var response = BridgeResponse(status: 500, json: ["ok": false, "error": "no answer"])

        init(_ request: BridgeRequest) { self.request = request }

        func finish(_ response: BridgeResponse) {
            self.response = response
            done.signal()
        }

        func wait() -> BridgeResponse {
            done.wait()
            return response
        }
    }

    private let condition = NSCondition()
    private var jobs: [Job] = []

    /// From a network thread: queue the request and wait for its answer.
    func submit(_ request: BridgeRequest) -> BridgeResponse {
        let job = Job(request)
        condition.lock()
        jobs.append(job)
        condition.signal()
        condition.unlock()
        return job.wait()
    }

    /// From the main thread: the next request, or nil after `timeout`.
    func next(timeout: TimeInterval) -> Job? {
        condition.lock()
        defer { condition.unlock() }
        if jobs.isEmpty {
            _ = condition.wait(until: Date(timeIntervalSinceNow: timeout))
        }
        return jobs.isEmpty ? nil : jobs.removeFirst()
    }
}
