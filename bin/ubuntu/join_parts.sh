#!/bin/bash
# Rebuild the original Linux executable from split parts

# Output file
OUTPUT_FILE="plot"

# Remove existing output if any
rm -f "$OUTPUT_FILE"

# Concatenate all parts in order
cat plot.part* > "$OUTPUT_FILE"

# Make the output executable
chmod +x "$OUTPUT_FILE"

echo "✅ Reconstructed '$OUTPUT_FILE' successfully."
