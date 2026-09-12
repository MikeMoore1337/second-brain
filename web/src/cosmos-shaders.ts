export const vertexSource = `attribute vec2 position;
void main() { gl_Position = vec4(position, 0., 1.); }`;
export const fragmentSource = `precision highp float;
uniform vec2 resolution;
uniform vec2 viewport;
uniform float time;
uniform float detail;
float hash(vec2 p) {
  vec3 q=fract(vec3(p.xyx)*.1031); q+=dot(q,q.yzx+33.33);
  return fract((q.x+q.y)*q.z);
}
// Lattice values evolve in place; no image, lookup texture or field translation.
float field(vec2 p,float age) {
  vec2 i=floor(p),f=fract(p); f=f*f*f*(f*(f*6.-15.)+10.);
  vec4 h=vec4(hash(i),hash(i+vec2(1,0)),hash(i+vec2(0,1)),hash(i+1.));
  vec4 v=.5+.5*sin(h*6.2831853+age*(.6+h*.7));
  return mix(mix(v.x,v.y,f.x),mix(v.z,v.w,f.x),f.y);
}
const mat2 turn=mat2(.8,-.6,.6,.8);
float fbm(vec2 p,float age,float footprint) {
  float sum=0.,weight=.5;
  for(int i=0;i<8;i++) {
    float visible=1.-smoothstep(.22,.65,footprint);
    if(float(i)<detail) sum+=weight*mix(.5,field(p,age),visible);
    else sum+=weight*.5;
    p=turn*p*2.03+vec2(17.7,9.2);
    footprint*=2.03; weight*=.5; age*=1.09;
  }
  return sum;
}
float ellipsoid(vec2 p,vec2 center,vec2 radius) {
  vec2 q=(p-center)/radius; return exp(-dot(q,q)*1.4);
}
float filament(vec2 p,float age,float footprint) {
  float result=0.,amplitude=.34,previous=1.;
  for(int i=0;i<8;i++) {
    float ridge=1.-abs(field(p,age)*2.-1.);
    ridge=ridge*ridge;
    float visible=1.-smoothstep(.24,.65,footprint);
    result+=amplitude*mix(.4,ridge*previous,visible);
    previous=mix(1.,ridge,.65);
    p=turn*p*2.13+19.1;footprint*=2.13;
    amplitude*=.67;age*=1.035;
  }
  return result;
}
float stars(vec2 p,float cells,float seed,float brightness,float parallax) {
  p+=vec2(sin(time*.009),sin(time*.0071))*.0012*parallax;
  vec2 grid=p*cells,i=floor(grid),f=fract(grid);
  float light=0.,pixel=cells/viewport.y;
  for(int y=-1;y<=1;y++) for(int x=-1;x<=1;x++) {
    vec2 cell=i+vec2(float(x),float(y)); float id=hash(cell+seed);
    vec2 center=vec2(hash(cell+seed+7.),hash(cell+seed+23.));
    vec2 d=f-vec2(float(x),float(y))-center;
    float radius=pixel*(.48+id*.35);
    float life=1.+.035*sin(time*(.09+id*.08)+id*83.);
    // Only a sparse subset participates. Each star has its own clock, and each
    // window independently decides whether to fade; no synchronized field pulse.
    float select=hash(cell+seed+151.);
    if(select>.90) {
      float period=24.+hash(cell+seed+181.)*24.;
      float clock=time+hash(cell+seed+199.)*period;
      float cycle=floor(clock/period),phase=mod(clock,period);
      float event=step(.5,hash(cell+vec2(seed+cycle*13.7,239.+cycle*7.1)));
      float duration=5.+hash(cell+seed+cycle+271.)*4.;
      float fade=smoothstep(0.,duration*.42,phase)
        *(1.-smoothstep(duration*.58,duration,phase));
      life*=1.-.92*event*fade;
    }
    light+=exp(-dot(d,d)/(radius*radius))*step(.87,id)*brightness*life;
  }
  return light;
}
void main() {
  vec2 p=(gl_FragCoord.xy-.5*resolution)/resolution.y;
  p.x+=viewport.x<600.? .12:0.;
  float pixel=1./resolution.y,age=time*.055;
  vec2 warp=vec2(field(p*2.1+13.,age*.37),field(p*2.3+37.,age*.31))-.5;
  vec2 q=p+warp*.13;
  float envelope=ellipsoid(q,vec2(.30,.15),vec2(.36,.55))
    +.85*ellipsoid(q,vec2(-.42,-.38),vec2(.47,.23))
    +.42*ellipsoid(q,vec2(.65,.54),vec2(.48,.22));
  float cavity=ellipsoid(q,vec2(-.36,.24),vec2(.42,.32))
    +.85*ellipsoid(q,vec2(.61,-.32),vec2(.36,.22));
  envelope*=exp(-cavity*3.6);
  float broad=fbm(q*3.4+5.,age*.61,pixel*3.4);
  float material=fbm(q*11.8+31.,age,pixel*11.8);
  float dust=fbm(q*7.3+103.,age*.79,pixel*7.3);
  // Independent absorption carves dark dust lanes THROUGH emitting material.
  float extinction=.20+.80*exp(-pow(max(0.,dust-.31)*2.7,2.)*1.5);
  float mass=envelope*(.15+broad*.55+material*.35)*1.5;
  float diffuse=mass*extinction;
  vec2 fiberCoords=mat2(.92,-.39,.39,.92)*q;
  float fibers=filament(fiberCoords*vec2(7.,4.)+71.,age*.65,pixel*7.);
  float threads=filament(q*21.+137.,age*.72,pixel*21.);
  float emission=diffuse*(.10+fibers*fibers*4.5+threads*.6);
  float depth=fbm(q*4.7+211.,age*.43,pixel*4.7);
  vec3 gas=mix(vec3(.16,.045,.29),vec3(.47,.20,.66),smoothstep(.12,.65,emission));
  gas*=emission*(.68+depth*.7)*1.7;
  gas=gas/(1.+gas);
  vec3 color=vec3(.0078,.0059,.0157)+gas;
  float starfield=stars(p,110.,17.,.25,0.)+stars(p,65.,53.,.43,.35)+stars(p,32.,97.,.68,1.);
  color+=vec3(.87,.76,1.)*starfield*mix(.3,1.,extinction);
  color+=(hash(gl_FragCoord.xy)-.5)/255.;
  gl_FragColor=vec4(clamp(color,0.,1.),1.);
}`;
