package dev.redstonecompiler.harness;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

final class HarnessValidationProgressTest {
    @Test
    void ReportsAuthoritativeTruthTableVectorCounts() {
        JsonObject Started = HarnessValidation.BuildValidationProgress(0, 512, 4);
        JsonObject Completed = HarnessValidation.BuildValidationProgress(512, 512, 4);

        assertEquals("progress", Started.get("Status").getAsString());
        assertEquals(0, Started.get("Completed").getAsInt());
        assertEquals(512, Started.get("Total").getAsInt());
        assertEquals(4, Started.get("ValidationLaneCount").getAsInt());
        assertEquals(
                "authoritative Fabric truth-table validation | lanes=4",
                Started.get("Stage").getAsString());
        assertEquals(512, Completed.get("Completed").getAsInt());
        assertEquals(512, Completed.get("Total").getAsInt());
    }

    @Test
    void BuildsFourVerticallyStackedValidationLaneOrigins() {
        JsonObject Fixture = JsonParser.parseString("""
                {
                  "Arena":{"Origin":[10,64,-5]},
                  "Blocks":[
                    {"Position":[0,1,0]},
                    {"Position":[9,6,19]}
                  ]
                }
                """).getAsJsonObject();

        List<com.google.gson.JsonArray> Origins =
                HarnessValidation.BuildValidationLaneOrigins(Fixture, 1, 4);

        assertEquals("[10,64,-5]", Origins.get(0).toString());
        assertEquals("[10,86,-5]", Origins.get(1).toString());
        assertEquals("[10,108,-5]", Origins.get(2).toString());
        assertEquals("[10,130,-5]", Origins.get(3).toString());
        assertThrows(
                IllegalArgumentException.class,
                () -> HarnessValidation.BuildValidationLaneOrigins(Fixture, 0, 4));
        assertThrows(
                IllegalArgumentException.class,
                () -> HarnessValidation.BuildValidationLaneOrigins(Fixture, 17, 4));
    }

    @Test
    void UsesTheMaximumUsefulLaneCountBeforeTpsCalibration() {
        assertEquals(
                64,
                HarnessValidation.BuildMaximumValidationLaneCount(
                        512, 4, 16, 1_000));
        assertEquals(
                5,
                HarnessValidation.BuildMaximumValidationLaneCount(
                        5, 4, 16, 1_000));
        assertEquals(
                32,
                HarnessValidation.BuildMaximumValidationLaneCount(
                        512, 4, 16, 5_000));

        HarnessValidation.ValidationLanePlan Rca4 =
                HarnessValidation.BuildValidationLanePlan(
                        512, 64, 4, 16, 200, 1000.0D);
        HarnessValidation.ValidationLanePlan Rca8 =
                HarnessValidation.BuildValidationLanePlan(
                        4132, 64, 4, 16, 200, 1000.0D);
        HarnessValidation.ValidationLanePlan PartialStack =
                HarnessValidation.BuildValidationLanePlan(
                        512, 37, 4, 16, 200, 1000.0D);

        assertEquals(16, Rca4.StackCount());
        assertEquals(64, Rca4.LaneCount());
        assertEquals(8, Rca4.BatchCount());
        assertEquals(1.6D, Rca4.TickBudgetSeconds(), 0.0001D);

        assertEquals(16, Rca8.StackCount());
        assertEquals(64, Rca8.LaneCount());
        assertEquals(65, Rca8.BatchCount());
        assertEquals(13.0D, Rca8.TickBudgetSeconds(), 0.0001D);

        assertEquals(10, PartialStack.StackCount());
        assertEquals(37, PartialStack.LaneCount());
        assertThrows(
                IllegalArgumentException.class,
                () -> HarnessValidation.BuildValidationLanePlan(
                        512, 65, 4, 16, 200, 1000.0D));
        assertThrows(
                IllegalArgumentException.class,
                () -> HarnessValidation.BuildValidationLanePlan(
                        512, 64, 4, 16, 0, 1000.0D));
    }

    @Test
    void AcceptsOnlyTickProcessingThatFitsTheRequestedTpsBudget() {
        HarnessValidation.ValidationTpsSample Exact =
                HarnessValidation.BuildValidationTpsSample(
                        64, 40, 40_000_000L, 1_200_000L, 1000.0D);
        HarnessValidation.ValidationTpsSample Slow =
                HarnessValidation.BuildValidationTpsSample(
                        63, 40, 40_040_000L, 1_200_000L, 1000.0D);

        assertEquals(1_000_000.0D, Exact.AverageTickProcessingNanos());
        assertEquals(1000.0D, Exact.SustainedTickRate());
        assertEquals(true, Exact.MeetsRequestedTickRate());
        assertEquals(false, Slow.MeetsRequestedTickRate());
        assertThrows(
                IllegalArgumentException.class,
                () -> HarnessValidation.BuildValidationTpsSample(
                        64, 0, 0L, 0L, 1000.0D));
    }

    @Test
    void ArrangesUpToSixteenVerticalStacksInAFourByFourArray() {
        JsonObject Fixture = JsonParser.parseString("""
                {
                  "Arena":{"Origin":[10,64,-5]},
                  "Blocks":[
                    {"Position":[0,1,0]},
                    {"Position":[9,6,19]}
                  ]
                }
                """).getAsJsonObject();

        List<com.google.gson.JsonArray> Origins =
                HarnessValidation.BuildValidationLaneOrigins(Fixture, 7, 4);

        assertEquals(28, Origins.size());
        assertEquals("[10,64,-5]", Origins.get(0).toString());
        assertEquals("[10,130,-5]", Origins.get(3).toString());
        assertEquals("[36,64,-5]", Origins.get(4).toString());
        assertEquals("[10,64,31]", Origins.get(16).toString());
        assertEquals("[62,130,31]", Origins.get(27).toString());

        List<com.google.gson.JsonArray> PartialOrigins =
                HarnessValidation.BuildValidationLaneOriginsForLaneCount(
                        Fixture, 5, 4);
        assertEquals(5, PartialOrigins.size());
        assertEquals("[36,64,-5]", PartialOrigins.get(4).toString());
    }
}
