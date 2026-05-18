package steps

import (
	"context"
)

type LoadIn struct {
	File string `json:"file"`
}

type LoadOut struct {
	Rows []struct { Name string `json:"name"`; Revenue float64 `json:"revenue"` } `json:"rows"`
}

// Load implements the 'load' step.
func Load(ctx context.Context, in LoadIn) (LoadOut, error) {
	panic("fill me in: load")
}
