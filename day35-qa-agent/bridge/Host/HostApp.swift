// The UI-testing bundle's target app. It is never what is tested: every test
// in QABridgeUITests drives another app by its bundle id.

import SwiftUI

@main
struct HostApp: App {
    var body: some Scene {
        WindowGroup {
            Text("QA bridge host")
        }
    }
}
