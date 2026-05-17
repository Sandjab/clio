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

func Load(ctx context.Context, in LoadIn) (LoadOut, error) {
	panic("fill me in: load")
}
