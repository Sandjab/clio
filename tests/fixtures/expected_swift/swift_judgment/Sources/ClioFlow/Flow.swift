import Foundation

public enum Flow {
    @MainActor
    public static func run(kwargs: [String: Any]) async throws -> [String: Any] {
        var state = kwargs

        let in1 = Step01_analyze_In(text: "Great product!")
        let out1 = try await step_analyze(in1)
        state["result"] = out1.result

        return state
    }
}
