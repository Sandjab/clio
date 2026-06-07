import Foundation

public enum Flow {
    @MainActor
    public static func run(kwargs: [String: Any]) async throws -> [String: Any] {
        var state = kwargs

        let in1 = Step01_load_In(file: "in.csv")
        let out1 = try await step_load(in1)
        state["items"] = out1.items

        let _items2 = state["items"] as! [String]
        var _collected2 = [Int: String](minimumCapacity: _items2.count)
        try await withThrowingTaskGroup(of: (Int, String).self) { group in
            var _inflight2 = 0
            for (_idx2, item) in _items2.enumerated() {
                if _inflight2 >= 10 {
                    if let (_i, _r) = try await group.next() {
                        _collected2[_i] = _r
                        _inflight2 -= 1
                    }
                }
                group.addTask {
                    let _in = Step02_classify_In(item: item)
                    let _out = try await step_classify(_in)
                    return (_idx2, _out.label)
                }
                _inflight2 += 1
            }
            while let (_i, _r) = try await group.next() {
                _collected2[_i] = _r
            }
        }
        state["labels"] = (0..<_items2.count).map { _collected2[$0]! }

        return state
    }
}
