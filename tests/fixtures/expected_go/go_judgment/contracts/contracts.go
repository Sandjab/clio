package contracts

import (
	"context"

	"pipeline/clio_runtime/validate"
)

type CustomerRisk struct {
	Client string `json:"client"`
	Risk string `json:"risk"`
	Reason string `json:"reason"`
}

const customerRiskSchema = `
{
  "type": "object",
  "properties": {
    "client": {
      "type": "string"
    },
    "risk": {
      "enum": [
        "low",
        "mid",
        "high"
      ]
    },
    "reason": {
      "type": "string",
      "maxLength": 300
    }
  },
  "required": [
    "client",
    "risk",
    "reason"
  ],
  "additionalProperties": false,
  "x-clio-assert": {
    "kind": "compare",
    "op": ">",
    "left": {
      "kind": "call",
      "func": "len",
      "args": [
        {
          "kind": "ident",
          "name": "reason"
        }
      ]
    },
    "right": {
      "kind": "int",
      "value": 0
    }
  }
}
`

func (c *CustomerRisk) Validate(ctx context.Context) error {
	return validate.Schema(ctx, customerRiskSchema, c)
}
