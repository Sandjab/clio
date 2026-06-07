import Foundation

public enum Flow {
    @MainActor
    public static func run(kwargs: [String: Any]) async throws -> [String: Any] {
        var state = kwargs

        let in1 = Step01_load_In(file: "customers.csv")
        let out1 = try await step_load(in1)
        state["clients"] = out1.clients

        let in2 = Step02_score_In(clients: state["clients"] as! [String])
        let out2 = try await step_score(in2)
        state["risk"] = out2.risk

        return state
    }
}
