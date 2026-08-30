package dev.redstonecompiler.harness.mixin;

import dev.redstonecompiler.harness.RedstoneCompilerHarness;
import net.minecraft.server.MinecraftServer;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(MinecraftServer.class)
abstract class MinecraftServerMixin {
    @Inject(method = "runServer", at = @At("HEAD"))
    private void redstonecompiler$CaptureServer(CallbackInfo callback) {
        RedstoneCompilerHarness.setServer((MinecraftServer) (Object) this);
    }
}
