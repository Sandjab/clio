package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"

	"customer_retention/flow"
)

func main() {
	kwargsRaw := flag.String("kwargs", "{}", "JSON-encoded kwargs for the flow")
	flag.Parse()

	var kwargs map[string]any
	if err := json.Unmarshal([]byte(*kwargsRaw), &kwargs); err != nil {
		fmt.Fprintf(os.Stderr, "invalid --kwargs: %v\n", err)
		os.Exit(2)
	}

	ctx := context.Background()
	state, err := flow.Run(ctx, kwargs)
	if err != nil {
		fmt.Fprintf(os.Stderr, "flow.Run: %v\n", err)
		os.Exit(1)
	}

	out, _ := json.MarshalIndent(state, "", "  ")
	fmt.Println(string(out))
}
