package steps

import (
	"context"

	"customer_retention/contracts"
)

type DetectChurnNaiveIn struct {
	Customers []struct { Name string `json:"name"`; Revenue float64 `json:"revenue"` } `json:"customers"`
}

type DetectChurnNaiveOut struct {
	Risks []contracts.CustomerRisk `json:"risks"`
}

// DetectChurnNaive implements the 'detect_churn_naive' step.
func DetectChurnNaive(ctx context.Context, in DetectChurnNaiveIn) (DetectChurnNaiveOut, error) {
	panic("fill me in: detect_churn_naive")
}
