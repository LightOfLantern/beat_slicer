package com.beatslicer.game;

import android.app.Activity;
import android.graphics.Rect;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.view.WindowManager;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import java.util.Arrays;

public final class MainActivity extends Activity {
    private WebView game;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        game = new WebView(this);
        game.setBackgroundColor(0xff07070c);
        game.setWebViewClient(new WebViewClient());
        game.setWebChromeClient(new WebChromeClient());

        WebSettings settings = game.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(true);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(false);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);

        setContentView(game);
        game.loadUrl("file:///android_asset/index.html");
        enterImmersiveMode();

        if (Build.VERSION.SDK_INT >= 29) {
            game.post(() -> {
                int width = game.getWidth();
                int height = game.getHeight();
                int edge = Math.max(24, Math.round(getResources().getDisplayMetrics().density * 24));
                game.setSystemGestureExclusionRects(Arrays.asList(
                    new Rect(0, 0, edge, height),
                    new Rect(Math.max(0, width - edge), 0, width, height)
                ));
            });
        }
    }

    private void enterImmersiveMode() {
        getWindow().getDecorView().setSystemUiVisibility(
            View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                | View.SYSTEM_UI_FLAG_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
        );
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) enterImmersiveMode();
    }

    @Override
    public void onBackPressed() {
        // A back swipe/button must not close an active game accidentally.
        enterImmersiveMode();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (game != null) game.onResume();
        enterImmersiveMode();
    }

    @Override
    protected void onPause() {
        if (game != null) game.onPause();
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        if (game != null) {
            game.stopLoading();
            game.destroy();
            game = null;
        }
        super.onDestroy();
    }
}

