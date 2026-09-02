package dev.redstonecompiler.harness;

import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

final class HarnessValidationProgressTest {
    @Test
    void ReportsAuthoritativeTruthTableVectorCounts() {
        JsonObject Started = HarnessValidation.BuildValidationProgress(0, 512);
        JsonObject Completed = HarnessValidation.BuildValidationProgress(512, 512);

        assertEquals("progress", Started.get("Status").getAsString());
        assertEquals(0, Started.get("Completed").getAsInt());
        assertEquals(512, Started.get("Total").getAsInt());
        assertEquals(
                "required single-fixture Fabric canary validation",
                Started.get("Stage").getAsString());
        assertEquals(512, Completed.get("Completed").getAsInt());
        assertEquals(512, Completed.get("Total").getAsInt());
        assertEquals(false, Started.has("ValidationLaneCount"));
    }
}
