package steps

import (
	"context"
)

type SummariseIn struct {
	Rows []struct { Name string `json:"name"`; Revenue float64 `json:"revenue"` } `json:"rows"`
}

type SummariseOut struct {
	Total float64 `json:"total"`
}

// Summarise implements the 'summarise' step.
func Summarise(ctx context.Context, in SummariseIn) (SummariseOut, error) {
	panic("fill me in: summarise")
}
