package dev.redstonecompiler.harness.mixin;

import dev.redstonecompiler.harness.RedstoneCompilerHarness;
import net.minecraft.server.MinecraftServer;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import java.util.function.BooleanSupplier;

@Mixin(MinecraftServer.class)
abstract class MinecraftServerMixin {
    @Unique
    private long redstonecompiler$TickStartedNanos;

    @Inject(method = "runServer", at = @At("HEAD"))
    private void redstonecompiler$CaptureServer(CallbackInfo callback) {
        RedstoneCompilerHarness.setServer((MinecraftServer) (Object) this);
    }

    @Inject(method = "tickServer", at = @At("TAIL"))
    private void redstonecompiler$ObserveServerTick(
            BooleanSupplier shouldKeepTicking,
            CallbackInfo callback) {
        RedstoneCompilerHarness.OnServerTick(
                (MinecraftServer) (Object) this,
                redstonecompiler$TickStartedNanos);
    }

    @Inject(method = "tickServer", at = @At("HEAD"))
    private void redstonecompiler$StartServerTickMeasurement(
            BooleanSupplier shouldKeepTicking,
            CallbackInfo callback) {
        redstonecompiler$TickStartedNanos = System.nanoTime();
    }
}
