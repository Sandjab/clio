import Foundation

public enum Flow {
    @MainActor
    public static func run(kwargs: [String: Any]) async throws -> [String: Any] {
        var state = kwargs

        let in1 = Step01_load_In(file: state["file"] as! String)
        let out1 = try await step_load(in1)
        state["rows"] = out1.rows

        let in2 = Step02_summarize_In(rows: state["rows"] as! [String])
        let out2 = try await step_summarize(in2)
        state["summary"] = out2.summary

        return state
    }
}
