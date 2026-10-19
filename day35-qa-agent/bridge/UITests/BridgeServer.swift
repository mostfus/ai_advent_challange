// A small HTTP/1.1 server for the bridge: one JSON request in, one JSON answer
// out, then the connection closes. It listens on 127.0.0.1 only - the
// simulator shares the Mac's loopback, so `qa/bridge.py` reaches it there and
// nothing else on the network can.
//
// The server never touches XCUITest itself. A request is handed to `handler`
// off the network queue, and the handler blocks until the test's main thread
// has run the command (`BridgeTests.serve`).

import Foundation
import Network

struct BridgeRequest {
    let method: String
    let path: String
    let body: [String: Any]
}

struct BridgeResponse {
    let status: Int
    let json: [String: Any]
}

final class BridgeServer {
    typealias Handler = (BridgeRequest) -> BridgeResponse

    private let listener: NWListener
    private let queue = DispatchQueue(label: "qa.bridge.server")
    private let handler: Handler
    private static let maxRequestBytes = 1 << 20

    init(port: UInt16, handler: @escaping Handler) throws {
        guard let nwPort = NWEndpoint.Port(rawValue: port) else {
            throw BridgeError("bad port \(port)")
        }
        let parameters = NWParameters.tcp
        parameters.allowLocalEndpointReuse = true
        parameters.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: nwPort)
        listener = try NWListener(using: parameters)
        self.handler = handler
    }

    func start() {
        listener.newConnectionHandler = { [weak self] connection in
            guard let self else { return }
            connection.start(queue: self.queue)
            self.receive(connection, buffer: Data())
        }
        listener.start(queue: queue)
    }

    func stop() {
        listener.cancel()
    }

    private func receive(_ connection: NWConnection, buffer: Data) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, isComplete, error in
            guard let self else { return }
            var buffer = buffer
            if let data { buffer.append(data) }
            if buffer.count > Self.maxRequestBytes {
                self.send(connection, BridgeResponse(status: 413, json: ["ok": false, "error": "request too large"]))
                return
            }
            switch Self.parse(buffer) {
            case .complete(let request):
                // Off the network queue: the handler waits for the main thread.
                DispatchQueue.global(qos: .userInitiated).async {
                    self.send(connection, self.handler(request))
                }
            case .invalid(let reason):
                self.send(connection, BridgeResponse(status: 400, json: ["ok": false, "error": reason]))
            case .incomplete:
                if isComplete || error != nil {
                    connection.cancel()
                } else {
                    self.receive(connection, buffer: buffer)
                }
            }
        }
    }

    private func send(_ connection: NWConnection, _ response: BridgeResponse) {
        let body = (try? JSONSerialization.data(withJSONObject: response.json, options: [])) ?? Data("{}".utf8)
        var head = "HTTP/1.1 \(response.status) \(response.status == 200 ? "OK" : "Error")\r\n"
        head += "Content-Type: application/json\r\n"
        head += "Content-Length: \(body.count)\r\n"
        head += "Connection: close\r\n\r\n"
        var data = Data(head.utf8)
        data.append(body)
        connection.send(content: data, completion: .contentProcessed { _ in connection.cancel() })
    }

    private enum Parsed {
        case incomplete
        case invalid(String)
        case complete(BridgeRequest)
    }

    private static func parse(_ data: Data) -> Parsed {
        let separator = Data("\r\n\r\n".utf8)
        guard let end = data.range(of: separator) else { return .incomplete }
        guard let head = String(data: data[..<end.lowerBound], encoding: .utf8) else {
            return .invalid("the request head is not UTF-8")
        }
        let lines = head.components(separatedBy: "\r\n")
        let parts = (lines.first ?? "").split(separator: " ")
        guard parts.count >= 2 else { return .invalid("bad request line") }
        var length = 0
        for line in lines.dropFirst() {
            let pair = line.split(separator: ":", maxSplits: 1).map { $0.trimmingCharacters(in: .whitespaces) }
            if pair.count == 2, pair[0].lowercased() == "content-length" {
                length = Int(pair[1]) ?? 0
            }
        }
        let bodyStart = end.upperBound
        guard data.count - bodyStart >= length else { return .incomplete }
        var body: [String: Any] = [:]
        if length > 0 {
            let raw = data[bodyStart..<(bodyStart + length)]
            guard let object = try? JSONSerialization.jsonObject(with: raw, options: []),
                  let dictionary = object as? [String: Any] else {
                return .invalid("the body is not a JSON object")
            }
            body = dictionary
        }
        let path = String(parts[1]).components(separatedBy: "?").first ?? "/"
        return .complete(BridgeRequest(method: String(parts[0]), path: path, body: body))
    }
}

struct BridgeError: Error, CustomStringConvertible {
    let description: String
    init(_ description: String) { self.description = description }
}
