<?php
declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
$configFile=__DIR__.'/config.php';
if(!is_file($configFile)){http_response_code(503);echo json_encode(['ok'=>false,'error'=>'backend_not_configured']);exit;}
$c=require $configFile;
$h=$_SERVER['HTTP_AUTHORIZATION']??'';$token='';if(preg_match('/^Bearer\s+(.+)$/i',$h,$m))$token=trim($m[1]);if($token===''||!hash_equals((string)($c['api_token']??''),$token)){http_response_code(401);echo json_encode(['ok'=>false,'error'=>'unauthorized']);exit;}
try{
 $pdo=new PDO('mysql:host='.$c['db_host'].';dbname='.$c['db_name'].';charset=utf8mb4',$c['db_user'],$c['db_pass'],[PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,PDO::ATTR_EMULATE_PREPARES=>false]);
 $sql=file_get_contents(__DIR__.'/schema.sql');if($sql===false)throw new RuntimeException('schema_missing');
 $parts=preg_split('/;\s*(?:\r?\n|$)/',$sql);$count=0;
 foreach($parts as $stmt){$stmt=trim($stmt);if($stmt==='')continue;$pdo->exec($stmt);$count++;}
 echo json_encode(['ok'=>true,'schemaStatements'=>$count,'time'=>gmdate('c')],JSON_UNESCAPED_SLASHES);
}catch(Throwable $e){error_log('7sky install: '.$e->getMessage());http_response_code(500);echo json_encode(['ok'=>false,'error'=>'install_failed']);}
