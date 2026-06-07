import Foundation

public enum Flow {
    @MainActor
    public static func run(kwargs: [String: Any]) async throws -> [String: Any] {
        var state = kwargs

        let in1 = Step01_detect_all_In(x: "incoming")
        let out1 = try await step_detect_all(in1)
        state["assessments"] = out1.assessments

        for a in (state["assessments"] as! [RiskAssessment]) {
            switch a.level {
                case "low":
                    let in2 = Step02_archive_In(x: "incoming")
                    let out2 = try await step_archive(in2)
                    state["archived"] = out2.archived

                case "mid":
                    let in3 = Step03_flag_In(x: "incoming")
                    let out3 = try await step_flag(in3)
                    state["flagged"] = out3.flagged

                case "high":
                    let in4 = Step03_flag_In(x: "incoming")
                    let out4 = try await step_flag(in4)
                    state["flagged"] = out4.flagged

                default: break
            }

        }

        for b in (state["assessments"] as! [RiskAssessment]) {
            if b.level == "high" {
                let in5 = Step03_flag_In(x: "incoming")
                let out5 = try await step_flag(in5)
                state["flagged"] = out5.flagged

            }

        }

        return state
    }
}
