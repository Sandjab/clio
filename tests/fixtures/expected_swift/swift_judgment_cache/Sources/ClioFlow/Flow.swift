import Foundation

public enum Flow {
    @MainActor
    public static func run(kwargs: [String: Any]) async throws -> [String: Any] {
        var state = kwargs

        let in1 = Step01_summarize_In(text: "Hello world")
        let out1 = try await step_summarize(in1)
        state["summary"] = out1.summary

        return state
    }
}
