import Foundation

public enum Flow {
    @MainActor
    public static func run(kwargs: [String: Any]) async throws -> [String: Any] {
        var state = kwargs

        let in1 = Step01_assess_In(x: "in")
        let out1 = try await step_assess(in1)
        state["r"] = out1.r

        switch (state["r"] as! Risk).level {
            case "low":
                let in2 = Step02_archive_In(x: "in")
                let out2 = try await step_archive(in2)
                state["archived"] = out2.archived

            case "mid":
                let in3 = Step03_flag_In(x: "in")
                let out3 = try await step_flag(in3)
                state["flagged"] = out3.flagged

            case "high":
                let in4 = Step03_flag_In(x: "in")
                let out4 = try await step_flag(in4)
                state["flagged"] = out4.flagged

            default: break
        }

        if (state["r"] as! Risk).score > 0.5 {
            let in5 = Step03_flag_In(x: "in")
            let out5 = try await step_flag(in5)
            state["flagged"] = out5.flagged

        } else {
            let in6 = Step02_archive_In(x: "in")
            let out6 = try await step_archive(in6)
            state["archived"] = out6.archived

        }

        let in7 = Step04_refine_In(x: "in")
        let out7 = try await step_refine(in7)
        state["p"] = out7.p

        var _while8 = 0
        while ((state["p"] as! Progress).done != true) && _while8 < 3 {
            let in9 = Step04_refine_In(x: "in")
            let out9 = try await step_refine(in9)
            state["p"] = out9.p

            _while8 += 1
        }

        return state
    }
}
