// What the generated scenario tests call (`qa/xcuitest.py` writes them into
// qa_suite/tests). A recorded step finds its element by what it was - its
// type and label - and waits for it, so a test survives a layout change that
// would break a tap by coordinates.

import XCTest

enum QA {
    static let timeout: TimeInterval = 10

    /// Launch the app under test from scratch.
    static func launch(_ bundleID: String) -> XCUIApplication {
        let app = XCUIApplication(bundleIdentifier: bundleID)
        if app.state != .notRunning { app.terminate() }
        app.launch()
        return app
    }

    /// Wait for the element, then tap it.
    static func tap(_ element: XCUIElement, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertTrue(element.waitForExistence(timeout: timeout), "Not found: \(element)", file: file, line: line)
        element.tap()
    }

    /// Wait for the element, then press and hold it.
    static func press(_ element: XCUIElement, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertTrue(element.waitForExistence(timeout: timeout), "Not found: \(element)", file: file, line: line)
        element.press(forDuration: 1.0)
    }

    /// The element is on the screen and holds this value (a switch's "1", a field's text).
    static func expectValue(_ element: XCUIElement, _ value: String, _ message: String,
                            file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertTrue(element.waitForExistence(timeout: timeout), message, file: file, line: line)
        XCTAssertEqual(element.value as? String, value, message, file: file, line: line)
    }

    /// Tap a point, for an element that had nothing to find it by.
    static func tap(_ app: XCUIApplication, x: Double, y: Double) {
        app.coordinate(withNormalizedOffset: CGVector(dx: 0, dy: 0)).withOffset(CGVector(dx: x, dy: y)).tap()
    }

    /// Type into the focused field.
    static func type(_ app: XCUIApplication, _ text: String) {
        app.typeText(text)
    }

    /// Drag from one point to another - a scroll.
    static func swipe(_ app: XCUIApplication, from: (Double, Double), to: (Double, Double)) {
        let origin = app.coordinate(withNormalizedOffset: CGVector(dx: 0, dy: 0))
        origin.withOffset(CGVector(dx: from.0, dy: from.1))
            .press(forDuration: 0.05, thenDragTo: origin.withOffset(CGVector(dx: to.0, dy: to.1)))
    }

    /// The element is on the screen.
    static func expect(_ element: XCUIElement, _ message: String, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertTrue(element.waitForExistence(timeout: timeout), message, file: file, line: line)
    }

    /// At least one element of `query` (a row, say) that is none of the known ones - by its
    /// label or the texts in it: "the list has rows", whichever rows today's data gives.
    static func expectRows(_ query: XCUIElementQuery, besides known: [String], _ message: String,
                           file: StaticString = #filePath, line: UInt = #line) {
        let deadline = Date().addingTimeInterval(timeout)
        repeat {
            for element in query.allElementsBoundByIndex where element.exists {
                let names = ([element.label] + element.staticTexts.allElementsBoundByIndex.map { $0.label })
                    .filter { !$0.isEmpty }
                if !names.isEmpty && !names.contains(where: { known.contains($0) }) { return }
            }
            Thread.sleep(forTimeInterval: 0.5)
        } while Date() < deadline
        XCTFail(message + " (no rows besides \(known))", file: file, line: line)
    }

    /// No element with this label or identifier is on the screen.
    static func expectAbsent(_ app: XCUIApplication, _ text: String, _ message: String,
                             file: StaticString = #filePath, line: UInt = #line) {
        let match = app.descendants(matching: .any).matching(NSPredicate(format: "label == %@ OR identifier == %@", text, text))
        let gone = NSPredicate(format: "count == 0")
        let expectation = XCTNSPredicateExpectation(predicate: gone, object: match)
        XCTAssertEqual(XCTWaiter().wait(for: [expectation], timeout: timeout), .completed, message, file: file, line: line)
    }
}
